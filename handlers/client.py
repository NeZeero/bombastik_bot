from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (Message, CallbackQuery, ReplyKeyboardMarkup,
                           KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton,
                           ReplyKeyboardRemove)
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
from html import escape
from time import monotonic

from database import (
    get_work_dates,
    get_slots_for_date,
    is_slot_free,
    create_booking,
    count_bookings_month_for_client,
)
from config import (
    MASTER_IDS,
    PORTFOLIO_TEXT,
    ADDRESS_TEXT,
    BOOKINGS_PER_USER_PER_MONTH,
    is_master,
)

router = Router()
_MENU_ACTION_DEBOUNCE_SECONDS = 0.8
_menu_action_timestamps: dict[tuple[int, str], float] = {}


class BookingFSM(StatesGroup):
    choosing_date = State()
    choosing_time = State()
    entering_name = State()
    entering_phone = State()


def _is_duplicate_menu_action(user_id: int, action: str) -> bool:
    now = monotonic()
    key = (user_id, action)
    last_seen = _menu_action_timestamps.get(key)
    _menu_action_timestamps[key] = now
    return last_seen is not None and now - last_seen < _MENU_ACTION_DEBOUNCE_SECONDS


async def _safe_edit_text(call: CallbackQuery, text: str, **kwargs):
    try:
        await call.message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Запись")],
            [KeyboardButton(text="📸 Портфолио"), KeyboardButton(text="📍 Наш адрес")],
        ],
        resize_keyboard=True
    )


def format_date_label(d: str) -> str:
    ru_days = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср",
               "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"}
    dt = datetime.strptime(d, "%Y-%m-%d")
    label = dt.strftime("%d.%m (%a)")
    for en, ru in ru_days.items():
        label = label.replace(en, ru)
    return label


def date_picker_keyboard(dates: list[str], per_row: int = 3) -> InlineKeyboardMarkup:
    """Несколько дат в ряд, чтобы список не тянулся в одну колонку."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for d in dates:
        row.append(
            InlineKeyboardButton(text=format_date_label(d), callback_data=f"date:{d}")
        )
        if len(row) >= per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── /start ────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! 👋\nДобро пожаловать! Выберите, что вас интересует:",
        reply_markup=main_keyboard()
    )


# ── Портфолио ─────────────────────────────────────────────
@router.message(F.text == "📸 Портфолио")
async def show_portfolio(message: Message):
    if _is_duplicate_menu_action(message.from_user.id, "portfolio"):
        return
    await message.answer(PORTFOLIO_TEXT, parse_mode="HTML")


# ── Адрес ─────────────────────────────────────────────────
@router.message(F.text == "📍 Наш адрес")
async def show_address(message: Message):
    if _is_duplicate_menu_action(message.from_user.id, "address"):
        return
    await message.answer(ADDRESS_TEXT, parse_mode="HTML")


# ── Начало записи — выбор даты ────────────────────────────
@router.message(F.text == "📅 Запись")
async def start_booking(message: Message, state: FSMContext):
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
        "📅 Выберите удобную дату:",
        reply_markup=date_picker_keyboard(dates),
    )
    await state.set_state(BookingFSM.choosing_date)


# ── Выбор времени ─────────────────────────────────────────
@router.callback_query(BookingFSM.choosing_date, F.data.startswith("date:"))
async def choose_time(call: CallbackQuery, state: FSMContext):
    await call.answer()
    # "date:YYYY-MM-DD" — только split с лимитом, иначе дата обрежется до года
    date = call.data.split(":", 1)[1]
    uid = call.from_user.id
    year_month = date[:7]
    if not is_master(uid) and count_bookings_month_for_client(
        uid, year_month
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
        await call.message.edit_text("На эту дату больше нет свободных окон.")
        return

    buttons = []
    for time, is_booked in slots:
        if is_booked:
            buttons.append([InlineKeyboardButton(text=f"❌ {time}", callback_data=f"busy:{time}")])
        else:
            buttons.append([InlineKeyboardButton(text=f"🟢 {time}", callback_data=f"time:{time}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к датам", callback_data="back_to_dates")])

    dt = datetime.strptime(date, "%Y-%m-%d")
    date_label = dt.strftime("%d.%m.%Y")
    await call.message.edit_text(
        f"🕐 Выберите время на <b>{date_label}</b>:\n\n"
        f"🟢 — свободно   ❌ — занято",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await state.set_state(BookingFSM.choosing_time)


@router.callback_query(F.data == "back_to_dates")
async def back_to_dates(call: CallbackQuery, state: FSMContext):
    dates = get_work_dates()
    if not dates:
        await call.message.edit_text(
            "К сожалению, сейчас нет доступных дат для записи.\n"
            "Попробуйте позже 🙏"
        )
        await state.clear()
        return
    await call.message.edit_text(
        "📅 Выберите удобную дату:",
        reply_markup=date_picker_keyboard(dates),
    )
    await state.set_state(BookingFSM.choosing_date)


@router.callback_query(BookingFSM.choosing_time, F.data.startswith("busy:"))
async def slot_busy(call: CallbackQuery):
    await call.answer("❌ Это время уже занято. Выберите другое.", show_alert=True)


# ── Ввод имени ────────────────────────────────────────────
@router.callback_query(BookingFSM.choosing_time, F.data.startswith("time:"))
async def enter_name(call: CallbackQuery, state: FSMContext):
    # "time:10:00" — нельзя split(":")[1], иначе получится только "10"
    time = call.data.removeprefix("time:")
    data = await state.get_data()
    date = data.get("chosen_date")

    if not is_slot_free(date, time):
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

    await state.update_data(chosen_time=time)
    await call.message.edit_text("✏️ Пожалуйста, укажите ваше имя:")
    await state.set_state(BookingFSM.entering_name)


# ── Ввод телефона ─────────────────────────────────────────
@router.message(BookingFSM.entering_name)
async def enter_phone(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("Пожалуйста, введите имя (минимум 2 символа):")
        return
    await state.update_data(client_name=name)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(
        f"Отлично, {name}! 👍\n\n"
        "Укажите контактный номер телефона\n"
        "или нажмите кнопку, чтобы поделиться автоматически:",
        reply_markup=kb
    )
    await state.set_state(BookingFSM.entering_phone)


# ── Завершение записи ─────────────────────────────────────
async def _finish_booking(message: Message, state: FSMContext, phone: str, bot: Bot):
    data = await state.get_data()
    date = data["chosen_date"]
    time = data["chosen_time"]
    name = data["client_name"]

    if not is_slot_free(date, time):
        await message.answer(
            "К сожалению, это время только что заняли 😔\n"
            "Пожалуйста, начните запись заново.",
            reply_markup=main_keyboard()
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
        time,
        name,
        phone,
        message.from_user.id,
        message.from_user.username,
    )

    dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    date_label = dt.strftime("%d.%m.%Y")

    await message.answer(
        f"✅ <b>Запись подтверждена!</b>\n\n"
        f"📅 Дата: <b>{date_label}</b>\n"
        f"🕐 Время: <b>{time}</b>\n"
        f"👤 Имя: <b>{name}</b>\n\n"
        "Мы напомним вам за 4 часа до визита. Ждём вас! 💅",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )

    u = message.from_user
    if u.username:
        tg_line = f"📱 Telegram: @{escape(u.username)}"
    else:
        tg_line = f"📱 Telegram: без username (id: <code>{u.id}</code>)"

    text = (
        f"🔔 <b>Новая запись!</b>\n\n"
        f"📅 <b>{date_label}</b> в <b>{time}</b>\n"
        f"👤 {escape(name)}\n"
        f"📞 {escape(phone)}\n"
        f"{tg_line}"
    )
    for mid in MASTER_IDS:
        try:
            await bot.send_message(mid, text, parse_mode="HTML")
        except Exception:
            pass

    await state.clear()


@router.message(BookingFSM.entering_phone, F.contact)
async def phone_via_contact(message: Message, state: FSMContext, bot: Bot):
    await _finish_booking(message, state, message.contact.phone_number, bot)


@router.message(BookingFSM.entering_phone, F.text)
async def phone_via_text(message: Message, state: FSMContext, bot: Bot):
    phone = message.text.strip()
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 10:
        await message.answer(
            "Пожалуйста, введите корректный номер (например: +79001234567):"
        )
        return
    await _finish_booking(message, state, phone, bot)
