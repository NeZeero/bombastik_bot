from datetime import datetime
from html import escape
import logging
from time import monotonic

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from config import (
    ADDRESS_TEXT,
    BOOKINGS_PER_USER_PER_MONTH,
    MASTER_IDS,
    PORTFOLIO_TEXT,
    is_master,
)
from database import (
    count_bookings_month_for_client,
    create_booking,
    get_last_client_name,
    get_slots_for_date,
    get_work_dates,
    is_slot_free,
)

router = Router()
logger = logging.getLogger(__name__)

BOOKING_BUTTON_TEXT = "📝 Запись"
PORTFOLIO_BUTTON_TEXT = "📸 Портфолио"
ADDRESS_BUTTON_TEXT = "📍 Наш адрес"
SHARE_CONTACT_BUTTON_TEXT = "📱 Поделиться номером"
CONFIRM_BOOKING_CALLBACK = "confirm_booking"
EDIT_BOOKING_CALLBACK = "edit_booking"

MENU_ACTION_DEBOUNCE_SECONDS = 0.8
_menu_action_timestamps: dict[tuple[int, str], float] = {}


class BookingFSM(StatesGroup):
    choosing_date = State()
    choosing_time = State()
    entering_name = State()
    entering_phone = State()
    confirming_booking = State()


def _normalize_menu_text(text: str | None) -> str:
    if not text:
        return ""
    normalized = text.casefold().strip()
    for ch in ("📝", "📸", "📍", "📱", "рџ“…", "рџ“ё", "рџ“Ќ", "рџ“±"):
        normalized = normalized.replace(ch.casefold(), "")
    return " ".join(normalized.split())


def _is_booking_text(text: str | None) -> bool:
    normalized = _normalize_menu_text(text)
    return "зап" in normalized or "рїрё" in normalized or "р°рї" in normalized


def _is_portfolio_text(text: str | None) -> bool:
    normalized = _normalize_menu_text(text)
    return "портф" in normalized or "с‚с„" in normalized or "сѓс‚с„" in normalized


def _is_address_text(text: str | None) -> bool:
    normalized = _normalize_menu_text(text)
    return "адрес" in normalized or "наш" in normalized or "р°рґс" in normalized


def _is_duplicate_menu_action(user_id: int, action: str) -> bool:
    now = monotonic()
    key = (user_id, action)
    previous = _menu_action_timestamps.get(key)
    _menu_action_timestamps[key] = now
    return previous is not None and now - previous < MENU_ACTION_DEBOUNCE_SECONDS


async def _safe_edit_text(call: CallbackQuery, text: str, **kwargs):
    try:
        await call.message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BOOKING_BUTTON_TEXT)],
            [
                KeyboardButton(text=PORTFOLIO_BUTTON_TEXT),
                KeyboardButton(text=ADDRESS_BUTTON_TEXT),
            ],
        ],
        resize_keyboard=True,
    )


def _name_keyboard(last_name: str | None):
    if not last_name:
        return ReplyKeyboardRemove()
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=last_name)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=SHARE_CONTACT_BUTTON_TEXT, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, совершить запись",
                    callback_data=CONFIRM_BOOKING_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет, изменить данные",
                    callback_data=EDIT_BOOKING_CALLBACK,
                )
            ],
        ]
    )


def format_date_label(date_str: str) -> str:
    ru_days = {
        "Mon": "Пн",
        "Tue": "Вт",
        "Wed": "Ср",
        "Thu": "Чт",
        "Fri": "Пт",
        "Sat": "Сб",
        "Sun": "Вс",
    }
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    label = dt.strftime("%d.%m (%a)")
    for en, ru in ru_days.items():
        label = label.replace(en, ru)
    return label


def date_picker_keyboard(dates: list[str], per_row: int = 3) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for date_str in dates:
        row.append(
            InlineKeyboardButton(
                text=format_date_label(date_str),
                callback_data=f"date:{date_str}",
            )
        )
        if len(row) >= per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_confirmation_text(date: str, time_value: str, name: str, phone: str) -> str:
    date_label = datetime.strptime(f"{date} {time_value}", "%Y-%m-%d %H:%M").strftime("%d.%m.%Y")
    return (
        "Проверьте, пожалуйста, данные записи:\n\n"
        f"📝 Дата: <b>{date_label}</b>\n"
        f"🕐 Время: <b>{time_value}</b>\n"
        f"👤 Имя: <b>{escape(name)}</b>\n"
        f"📞 Номер: <b>{escape(phone)}</b>\n\n"
        "Всё верно?"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! 👋\nДобро пожаловать! Выберите, что вас интересует:",
        reply_markup=main_keyboard(),
    )


@router.message(lambda message: _is_portfolio_text(message.text))
async def show_portfolio(message: Message):
    logger.info("Menu action: portfolio text=%r", message.text)
    if _is_duplicate_menu_action(message.from_user.id, "portfolio"):
        return
    await message.answer(PORTFOLIO_TEXT, parse_mode="HTML")


@router.message(lambda message: _is_address_text(message.text))
async def show_address(message: Message):
    logger.info("Menu action: address text=%r", message.text)
    if _is_duplicate_menu_action(message.from_user.id, "address"):
        return
    await message.answer(ADDRESS_TEXT, parse_mode="HTML")


@router.message(lambda message: _is_booking_text(message.text))
async def start_booking(message: Message, state: FSMContext):
    logger.info("Menu action: booking text=%r", message.text)
    if _is_duplicate_menu_action(message.from_user.id, "booking"):
        return

    dates = get_work_dates()
    if not dates:
        await message.answer(
            "К сожалению, сейчас нет доступных дат для записи.\n"
            "Попробуйте позже 🙏"
        )
        return

    await message.answer(
        "📝 Выберите удобную дату:",
        reply_markup=date_picker_keyboard(dates),
    )
    await state.set_state(BookingFSM.choosing_date)


@router.callback_query(BookingFSM.choosing_date, F.data.startswith("date:"))
async def choose_time(call: CallbackQuery, state: FSMContext):
    await call.answer()
    date = call.data.split(":", 1)[1]
    year_month = date[:7]
    user_id = call.from_user.id

    if not is_master(user_id) and count_bookings_month_for_client(
        user_id, year_month
    ) >= BOOKINGS_PER_USER_PER_MONTH:
        await call.answer(
            f"В этом месяце можно не более {BOOKINGS_PER_USER_PER_MONTH} записей. "
            "Выберите другой месяц или дождитесь следующего.",
            show_alert=True,
        )
        return

    await state.update_data(chosen_date=date)
    slots = get_slots_for_date(date)
    if not slots:
        await _safe_edit_text(call, "На эту дату больше нет свободных окон.")
        return

    buttons: list[list[InlineKeyboardButton]] = []
    for time_value, is_booked in slots:
        label = f"❌ {time_value}" if is_booked else f"🟢 {time_value}"
        callback_data = f"busy:{time_value}" if is_booked else f"time:{time_value}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=callback_data)])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к датам", callback_data="back_to_dates")])

    date_label = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
    await _safe_edit_text(
        call,
        f"🕐 Выберите время на <b>{date_label}</b>:\n\n"
        "🟢 — свободно   ❌ — занято",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
    await state.set_state(BookingFSM.choosing_time)


@router.callback_query(F.data == "back_to_dates")
async def back_to_dates(call: CallbackQuery, state: FSMContext):
    await call.answer()
    dates = get_work_dates()
    if not dates:
        await _safe_edit_text(
            call,
            "К сожалению, сейчас нет доступных дат для записи.\nПопробуйте позже 🙏",
        )
        await state.clear()
        return

    await _safe_edit_text(
        call,
        "📝 Выберите удобную дату:",
        reply_markup=date_picker_keyboard(dates),
    )
    await state.set_state(BookingFSM.choosing_date)


@router.callback_query(BookingFSM.choosing_time, F.data.startswith("busy:"))
async def slot_busy(call: CallbackQuery):
    await call.answer("❌ Это время уже занято. Выберите другое.", show_alert=True)


@router.callback_query(BookingFSM.choosing_time, F.data.startswith("time:"))
async def enter_name(call: CallbackQuery, state: FSMContext):
    await call.answer()
    time_value = call.data.removeprefix("time:")
    data = await state.get_data()
    date = data.get("chosen_date")

    if not date:
        await state.clear()
        await call.answer("Сессия записи устарела. Начните заново.", show_alert=True)
        return

    if not is_slot_free(date, time_value):
        await call.answer("Это время только что заняли. Выберите другое.", show_alert=True)
        return

    year_month = date[:7]
    if not is_master(call.from_user.id) and count_bookings_month_for_client(
        call.from_user.id, year_month
    ) >= BOOKINGS_PER_USER_PER_MONTH:
        await call.answer(
            f"Лимит {BOOKINGS_PER_USER_PER_MONTH} записей в этом месяце уже достигнут.",
            show_alert=True,
        )
        return

    await state.update_data(chosen_time=time_value)
    last_name = get_last_client_name(call.from_user.id)
    await _safe_edit_text(call, "✏️ Пожалуйста, укажите ваше имя:")
    await call.message.answer(
        "Можно ввести имя вручную или нажать кнопку ниже.",
        reply_markup=_name_keyboard(last_name),
    )
    await state.set_state(BookingFSM.entering_name)


@router.message(BookingFSM.entering_name)
async def enter_phone(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Пожалуйста, введите имя (минимум 2 символа):")
        return

    await state.update_data(client_name=name)
    await message.answer(
        f"Отлично, {name}! 👍\n\n"
        "Укажите контактный номер телефона\n"
        "или нажмите кнопку, чтобы поделиться автоматически:",
        reply_markup=_contact_keyboard(),
    )
    await state.set_state(BookingFSM.entering_phone)


async def _show_confirmation(message: Message, state: FSMContext, phone: str):
    data = await state.get_data()
    date = data["chosen_date"]
    time_value = data["chosen_time"]
    name = data["client_name"]
    await state.update_data(client_phone=phone)
    await message.answer(
        _build_confirmation_text(date, time_value, name, phone),
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "Подтвердите запись или вернитесь к изменению данных.",
        reply_markup=_confirmation_keyboard(),
    )
    await state.set_state(BookingFSM.confirming_booking)


async def _finish_booking(
    message: Message,
    state: FSMContext,
    phone: str | None = None,
    bot: Bot | None = None,
):
    data = await state.get_data()
    date = data["chosen_date"]
    time_value = data["chosen_time"]
    name = data["client_name"]
    phone_value = phone or data["client_phone"]

    if not is_slot_free(date, time_value):
        await message.answer(
            "К сожалению, это время только что заняли 😔\nПожалуйста, начните запись заново.",
            reply_markup=main_keyboard(),
        )
        await state.clear()
        return

    year_month = date[:7]
    if not is_master(message.from_user.id) and count_bookings_month_for_client(
        message.from_user.id, year_month
    ) >= BOOKINGS_PER_USER_PER_MONTH:
        await message.answer(
            f"В этом месяце можно оформить не более {BOOKINGS_PER_USER_PER_MONTH} записей.\n"
            "Попробуйте другой месяц или обратитесь к мастеру.",
            reply_markup=main_keyboard(),
        )
        await state.clear()
        return

    create_booking(
        date,
        time_value,
        name,
        phone_value,
        message.from_user.id,
        message.from_user.username,
    )

    date_label = datetime.strptime(f"{date} {time_value}", "%Y-%m-%d %H:%M").strftime("%d.%m.%Y")
    await message.answer(
        f"✅ <b>Запись подтверждена!</b>\n\n"
        f"📝 Дата: <b>{date_label}</b>\n"
        f"🕐 Время: <b>{time_value}</b>\n"
        f"👤 Имя: <b>{escape(name)}</b>\n"
        f"📞 Номер: <b>{escape(phone_value)}</b>\n\n"
        "Мы напомним вам за 4 часа до визита. Ждём вас! 💅",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

    if bot is None:
        bot = message.bot

    user = message.from_user
    if user.username:
        tg_line = f"📱 Telegram: @{escape(user.username)}"
    else:
        tg_line = f"📱 Telegram: без username (id: <code>{user.id}</code>)"

    master_text = (
        f"🔔 <b>Новая запись!</b>\n\n"
        f"📝 <b>{date_label}</b> в <b>{time_value}</b>\n"
        f"👤 {escape(name)}\n"
        f"📞 {escape(phone_value)}\n"
        f"{tg_line}"
    )
    for master_id in MASTER_IDS:
        try:
            await bot.send_message(master_id, master_text, parse_mode="HTML")
        except Exception:
            pass

    await state.clear()


@router.message(BookingFSM.entering_phone, F.contact)
async def phone_via_contact(message: Message, state: FSMContext):
    await _show_confirmation(message, state, message.contact.phone_number)


@router.message(BookingFSM.entering_phone, F.text)
async def phone_via_text(message: Message, state: FSMContext):
    phone = (message.text or "").strip()
    digits = "".join(char for char in phone if char.isdigit())
    if len(digits) < 10:
        await message.answer(
            "Пожалуйста, введите корректный номер (например: +79001234567):"
        )
        return
    await _show_confirmation(message, state, phone)


@router.callback_query(BookingFSM.confirming_booking, F.data == CONFIRM_BOOKING_CALLBACK)
async def confirm_booking(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await _safe_edit_text(call, "Запись подтверждается...")
    await _finish_booking(call.message, state)


@router.callback_query(BookingFSM.confirming_booking, F.data == EDIT_BOOKING_CALLBACK)
async def edit_booking(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    last_name = data.get("client_name") or get_last_client_name(call.from_user.id)
    await _safe_edit_text(call, "Изменение данных. Введите имя заново:")
    await call.message.answer(
        "Можно ввести новое имя или нажать кнопку с последним вариантом.",
        reply_markup=_name_keyboard(last_name),
    )
    await state.set_state(BookingFSM.entering_name)


@router.callback_query(F.data.startswith("date:"))
async def stale_date_callback(call: CallbackQuery, state: FSMContext):
    await call.answer("Это меню уже устарело. Нажмите «Запись» ещё раз.", show_alert=True)
    await state.clear()


@router.callback_query(F.data.startswith("time:"))
async def stale_time_callback(call: CallbackQuery, state: FSMContext):
    await call.answer("Это меню уже устарело. Нажмите «Запись» ещё раз.", show_alert=True)
    await state.clear()


@router.callback_query(F.data.startswith("busy:"))
async def stale_busy_callback(call: CallbackQuery, state: FSMContext):
    await call.answer("Список времени уже обновился. Начните выбор заново.", show_alert=True)
    await state.clear()


@router.message()
async def debug_incoming_messages(message: Message):
    logger.info("Incoming message text=%r contact=%r", message.text, bool(message.contact))
