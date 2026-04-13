from __future__ import annotations

import logging
from datetime import datetime
from html import escape
from time import monotonic

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from config import (
    ADDRESS_TEXT,
    BOOKINGS_PER_USER_PER_MONTH,
    MASTER_IDS,
    PORTFOLIO_TEXT,
    calculate_booking_duration,
    format_service_names,
    get_service,
    get_service_categories,
    get_service_category,
    is_master,
)
from database import count_bookings_month_for_client, create_booking, get_available_dates, get_last_client_name, get_slots_for_date, is_slot_free

router = Router()
logger = logging.getLogger(__name__)

DATES_PAGE_SIZE = 30
BOOKING_BUTTON_TEXT = "📝 Запись"
PORTFOLIO_BUTTON_TEXT = "📸 Портфолио"
ADDRESS_BUTTON_TEXT = "📍 Наш адрес"
SHARE_CONTACT_BUTTON_TEXT = "📱 Поделиться номером"
CONFIRM_BOOKING_CALLBACK = "confirm_booking"
EDIT_BOOKING_CALLBACK = "edit_booking"
BACK_TO_TIME_CALLBACK = "back_to_time"
BOOKING_CANCEL_CALLBACK = "booking_cancel"
SERVICE_MORE_CALLBACK = "service_more"
SERVICE_DATES_CALLBACK = "service_dates"
BACK_TO_SERVICES_CALLBACK = "back_to_services"

MENU_ACTION_DEBOUNCE_SECONDS = 0.8
_menu_action_timestamps: dict[tuple[int, str], float] = {}


class BookingFSM(StatesGroup):
    choosing_category = State()
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    entering_name = State()
    entering_phone = State()
    confirming_booking = State()


def _normalize_menu_text(text: str | None) -> str:
    if not text:
        return ""
    normalized = text.casefold().strip()
    for ch in ("📝", "📸", "📍", "📱"):
        normalized = normalized.replace(ch.casefold(), "")
    return " ".join(normalized.split())


def _is_booking_text(text: str | None) -> bool:
    return "зап" in _normalize_menu_text(text)


def _is_portfolio_text(text: str | None) -> bool:
    return "портф" in _normalize_menu_text(text)


def _is_address_text(text: str | None) -> bool:
    normalized = _normalize_menu_text(text)
    return "адрес" in normalized or "наш" in normalized


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
            [KeyboardButton(text=PORTFOLIO_BUTTON_TEXT), KeyboardButton(text=ADDRESS_BUTTON_TEXT)],
        ],
        resize_keyboard=True,
    )


def _name_keyboard(last_name: str | None):
    if not last_name:
        return ReplyKeyboardRemove()
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=last_name)]], resize_keyboard=True, one_time_keyboard=True)


def _contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=SHARE_CONTACT_BUTTON_TEXT, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, подтвердить запись", callback_data=CONFIRM_BOOKING_CALLBACK)],
            [InlineKeyboardButton(text="Изменить имя/номер", callback_data=EDIT_BOOKING_CALLBACK)],
            [InlineKeyboardButton(text="◀️ Вернуться к выбору времени", callback_data=BACK_TO_TIME_CALLBACK)],
        ]
    )


def _back_to_time_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад к выбору времени", callback_data=BACK_TO_TIME_CALLBACK)]])


def _category_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=category["title"], callback_data=f"svc_cat:{category['id']}")] for category in get_service_categories()]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=BOOKING_CANCEL_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _service_keyboard(category_id: str) -> InlineKeyboardMarkup:
    category = get_service_category(category_id)
    rows = []
    for service in category["services"]:
        rows.append([InlineKeyboardButton(text=f"{service['title']} • {service['duration_min']} мин.", callback_data=f"svc_item:{service['id']}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад к категориям", callback_data="svc_back_categories")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _services_ready_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить ещё услугу", callback_data=SERVICE_MORE_CALLBACK)],
            [InlineKeyboardButton(text="Перейти к выбору даты", callback_data=SERVICE_DATES_CALLBACK)],
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data=BOOKING_CANCEL_CALLBACK)],
        ]
    )


def _selected_services(data: dict) -> list[str]:
    return list(data.get("selected_service_ids", []))


def _selected_duration(data: dict) -> int:
    return calculate_booking_duration(_selected_services(data))


def _selected_services_text(data: dict) -> str:
    services = _selected_services(data)
    return format_service_names(services) if services else "—"


def _services_summary_text(data: dict) -> str:
    return f"Услуги: <b>{_selected_services_text(data)}</b>\n⏱ Общее время: <b>{_selected_duration(data)} мин.</b>"


def format_date_label(date_str: str) -> str:
    ru_days = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"}
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    label = dt.strftime("%d.%m (%a)")
    for en, ru in ru_days.items():
        label = label.replace(en, ru)
    return label


def _date_picker_keyboard(dates: list[str], *, user_id: int, page: int) -> InlineKeyboardMarkup:
    safe_page = max(page, 0)
    start = safe_page * DATES_PAGE_SIZE
    page_dates = dates[start:start + DATES_PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for date_str in page_dates:
        callback_data = f"limit_date:{date_str}" if _user_reached_booking_limit(user_id, date_str) else f"date:{date_str}"
        row.append(InlineKeyboardButton(text=format_date_label(date_str), callback_data=callback_data))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav_row: list[InlineKeyboardButton] = []
    if safe_page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"dates_page:{safe_page - 1}"))
    if start + DATES_PAGE_SIZE < len(dates):
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"dates_page:{safe_page + 1}"))
    if nav_row:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton(text="◀️ К услугам", callback_data=BACK_TO_SERVICES_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _time_picker_keyboard(slots: list[tuple[str, bool]], *, per_row: int = 3) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for time_value, is_busy in slots:
        label = f"❌ {time_value}" if is_busy else f"🟢 {time_value}"
        callback_data = f"busy:{time_value}" if is_busy else f"time:{time_value}"
        row.append(InlineKeyboardButton(text=label, callback_data=callback_data))
        if len(row) >= per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Назад к датам", callback_data="back_to_dates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_confirmation_text(data: dict, name: str, phone: str) -> str:
    date = data["chosen_date"]
    time_value = data["chosen_time"]
    date_label = datetime.strptime(f"{date} {time_value}", "%Y-%m-%d %H:%M").strftime("%d.%m.%Y")
    return (
        "Проверьте, пожалуйста, данные записи:\n\n"
        f"Услуги: <b>{escape(_selected_services_text(data))}</b>\n"
        f"⏱ Длительность: <b>{_selected_duration(data)} мин.</b>\n"
        f"🗓 Дата: <b>{date_label}</b>\n"
        f"🕐 Время: <b>{time_value}</b>\n"
        f"👤 Имя: <b>{escape(name)}</b>\n"
        f"📞 Номер: <b>{escape(phone)}</b>"
    )


def _user_reached_booking_limit(user_id: int, date: str) -> bool:
    return not is_master(user_id) and count_bookings_month_for_client(user_id, date[:7]) >= BOOKINGS_PER_USER_PER_MONTH


async def _show_category_picker(target: Message | CallbackQuery, state: FSMContext, *, edit: bool = False):
    data = await state.get_data()
    text = f"💅 Выберите категорию услуги.\n\n{_services_summary_text(data)}"
    if isinstance(target, CallbackQuery):
        if edit:
            await _safe_edit_text(target, text, parse_mode="HTML", reply_markup=_category_keyboard())
        else:
            await target.message.answer(text, parse_mode="HTML", reply_markup=_category_keyboard())
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=_category_keyboard())
    await state.set_state(BookingFSM.choosing_category)


async def _show_service_picker(call: CallbackQuery, state: FSMContext, category_id: str):
    category = get_service_category(category_id)
    if not category:
        await call.answer("Категория не найдена.", show_alert=True)
        return
    await state.update_data(current_category_id=category_id)
    data = await state.get_data()
    text = f"<b>{category['title']}</b>\n\nВыберите услугу:\n\n{_services_summary_text(data)}"
    await _safe_edit_text(call, text, parse_mode="HTML", reply_markup=_service_keyboard(category_id))
    await state.set_state(BookingFSM.choosing_service)


async def _show_services_ready(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await _safe_edit_text(
        call,
        "Хотите добавить ещё услугу или уже перейти к выбору даты?\n\n" + _services_summary_text(data),
        parse_mode="HTML",
        reply_markup=_services_ready_keyboard(),
    )
    await state.set_state(BookingFSM.choosing_category)


async def _show_date_picker(call: CallbackQuery, state: FSMContext, *, page: int = 0):
    data = await state.get_data()
    dates = get_available_dates(_selected_duration(data))
    if not dates:
        await _safe_edit_text(
            call,
            "Для выбранных услуг сейчас нет доступных дат.\n\n" + _services_summary_text(data),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К услугам", callback_data=BACK_TO_SERVICES_CALLBACK)]]),
        )
        await state.set_state(BookingFSM.choosing_date)
        return
    max_page = max((len(dates) - 1) // DATES_PAGE_SIZE, 0)
    page = min(max(page, 0), max_page)
    await state.update_data(date_page=page)
    await _safe_edit_text(
        call,
        "🗓 Выберите удобную дату:\n\n" + _services_summary_text(data),
        parse_mode="HTML",
        reply_markup=_date_picker_keyboard(dates, user_id=call.from_user.id, page=page),
    )
    await state.set_state(BookingFSM.choosing_date)


async def _show_time_picker(call: CallbackQuery, state: FSMContext, date: str):
    data = await state.get_data()
    duration = _selected_duration(data)
    slots = get_slots_for_date(date, duration)
    if not slots:
        await _safe_edit_text(call, "На эту дату нет подходящего времени для выбранных услуг.")
        await state.set_state(BookingFSM.choosing_date)
        return
    date_label = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
    await _safe_edit_text(
        call,
        f"🕐 Выберите время на <b>{date_label}</b>:\n\n{_services_summary_text(data)}\n\n🟢 — свободно   ❌ — занято",
        parse_mode="HTML",
        reply_markup=_time_picker_keyboard(slots),
    )
    await state.set_state(BookingFSM.choosing_time)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Привет! 👋\nДобро пожаловать! Выберите, что вас интересует:", reply_markup=main_keyboard())


@router.message(lambda message: _is_portfolio_text(message.text))
async def show_portfolio(message: Message):
    if _is_duplicate_menu_action(message.from_user.id, "portfolio"):
        return
    await message.answer(PORTFOLIO_TEXT, parse_mode="HTML")


@router.message(lambda message: _is_address_text(message.text))
async def show_address(message: Message):
    if _is_duplicate_menu_action(message.from_user.id, "address"):
        return
    await message.answer(ADDRESS_TEXT, parse_mode="HTML")


@router.message(lambda message: _is_booking_text(message.text))
async def start_booking(message: Message, state: FSMContext):
    if _is_duplicate_menu_action(message.from_user.id, "booking"):
        return
    await state.clear()
    await state.update_data(selected_service_ids=[], date_page=0)
    await _show_category_picker(message, state)


@router.callback_query(F.data == BOOKING_CANCEL_CALLBACK)
async def booking_cancel(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await _safe_edit_text(call, "Запись отменена.")
    await call.message.answer("Главное меню:", reply_markup=main_keyboard())


@router.callback_query(BookingFSM.choosing_category, F.data.startswith("svc_cat:"))
async def choose_category(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await _show_service_picker(call, state, call.data.split(":", 1)[1])


@router.callback_query(BookingFSM.choosing_service, F.data == "svc_back_categories")
async def services_back_to_categories(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await _show_category_picker(call, state, edit=True)


@router.callback_query(BookingFSM.choosing_service, F.data.startswith("svc_item:"))
async def choose_service(call: CallbackQuery, state: FSMContext):
    await call.answer()
    service_id = call.data.split(":", 1)[1]
    service = get_service(service_id)
    if not service:
        await call.answer("Услуга не найдена.", show_alert=True)
        return
    data = await state.get_data()
    selected = _selected_services(data)
    if service_id in selected:
        await call.answer("Эта услуга уже выбрана.", show_alert=True)
        return
    selected.append(service_id)
    await state.update_data(selected_service_ids=selected)
    await _show_services_ready(call, state)


@router.callback_query(BookingFSM.choosing_category, F.data == SERVICE_MORE_CALLBACK)
async def choose_more_services(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await _show_category_picker(call, state, edit=True)


@router.callback_query(BookingFSM.choosing_category, F.data == SERVICE_DATES_CALLBACK)
async def proceed_to_dates(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    if not _selected_services(data):
        await call.answer("Сначала выберите хотя бы одну услугу.", show_alert=True)
        return
    await _show_date_picker(call, state, page=0)


@router.callback_query(BookingFSM.choosing_date, F.data == BACK_TO_SERVICES_CALLBACK)
async def back_to_services(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await _show_services_ready(call, state)


@router.callback_query(BookingFSM.choosing_date, F.data.startswith("dates_page:"))
async def paginate_dates(call: CallbackQuery, state: FSMContext):
    await call.answer()
    try:
        page = int(call.data.split(":", 1)[1])
    except ValueError:
        page = 0
    await _show_date_picker(call, state, page=page)


@router.callback_query(BookingFSM.choosing_date, F.data.startswith("limit_date:"))
async def limited_date_alert(call: CallbackQuery):
    await call.answer(
        f"В этом месяце у вас уже есть максимальные {BOOKINGS_PER_USER_PER_MONTH} записи. Выберите дату в другом месяце.",
        show_alert=True,
    )


@router.callback_query(BookingFSM.choosing_date, F.data.startswith("date:"))
async def choose_time(call: CallbackQuery, state: FSMContext):
    await call.answer()
    date = call.data.split(":", 1)[1]
    if _user_reached_booking_limit(call.from_user.id, date):
        await call.answer(f"В этом месяце можно не более {BOOKINGS_PER_USER_PER_MONTH} записей.", show_alert=True)
        return
    await state.update_data(chosen_date=date)
    await _show_time_picker(call, state, date)


@router.callback_query(F.data == "back_to_dates")
async def back_to_dates(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    page = int(data.get("date_page", 0) or 0)
    await _show_date_picker(call, state, page=page)


@router.callback_query(BookingFSM.choosing_time, F.data.startswith("busy:"))
async def slot_busy(call: CallbackQuery):
    await call.answer("Это время уже занято или не подходит по длительности услуги.", show_alert=True)


@router.callback_query(BookingFSM.choosing_time, F.data.startswith("time:"))
async def enter_name(call: CallbackQuery, state: FSMContext):
    await call.answer()
    time_value = call.data.removeprefix("time:")
    data = await state.get_data()
    date = data.get("chosen_date")
    duration = _selected_duration(data)
    if not date:
        await state.clear()
        await call.answer("Сессия записи устарела. Начните заново.", show_alert=True)
        return
    if not is_slot_free(date, time_value, duration):
        await call.answer("Это время только что стало недоступно. Выберите другое.", show_alert=True)
        return
    await state.update_data(chosen_time=time_value)
    last_name = get_last_client_name(call.from_user.id)
    await _safe_edit_text(call, "✏️ Пожалуйста, укажите ваше имя:")
    await call.message.answer("Можно ввести имя вручную или нажать кнопку ниже.", reply_markup=_name_keyboard(last_name))
    await call.message.answer("Если нужно, можно вернуться к выбору времени.", reply_markup=_back_to_time_keyboard())
    await state.set_state(BookingFSM.entering_name)


@router.message(BookingFSM.entering_name)
async def enter_phone(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Пожалуйста, введите имя минимум из 2 символов:")
        return
    await state.update_data(client_name=name)
    await message.answer(
        f"Отлично, {name}! 👌\n\nУкажите контактный номер телефона или нажмите кнопку, чтобы поделиться автоматически:",
        reply_markup=_contact_keyboard(),
    )
    await message.answer("Если нужно, можно вернуться к выбору времени.", reply_markup=_back_to_time_keyboard())
    await state.set_state(BookingFSM.entering_phone)


async def _show_confirmation(message: Message, state: FSMContext, phone: str):
    data = await state.get_data()
    await state.update_data(client_phone=phone)
    await message.answer(_build_confirmation_text(data, data["client_name"], phone), parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    await message.answer("Подтвердите запись или вернитесь к выбору времени.", reply_markup=_confirmation_keyboard())
    await state.set_state(BookingFSM.confirming_booking)


@router.callback_query(BookingFSM.entering_name, F.data == BACK_TO_TIME_CALLBACK)
@router.callback_query(BookingFSM.entering_phone, F.data == BACK_TO_TIME_CALLBACK)
@router.callback_query(BookingFSM.confirming_booking, F.data == BACK_TO_TIME_CALLBACK)
async def back_to_time(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    date = data.get("chosen_date")
    if not date:
        await state.clear()
        await call.answer("Сессия записи устарела. Начните заново.", show_alert=True)
        return
    await _show_time_picker(call, state, date)


async def _finish_booking(message: Message, state: FSMContext, booking_user, phone: str | None = None, bot: Bot | None = None):
    data = await state.get_data()
    date = data["chosen_date"]
    time_value = data["chosen_time"]
    name = data["client_name"]
    phone_value = phone or data["client_phone"]
    service_ids = _selected_services(data)
    service_names = _selected_services_text(data)
    duration = _selected_duration(data)

    if not is_slot_free(date, time_value, duration):
        await message.answer("К сожалению, это время уже заняли. Пожалуйста, начните запись заново.", reply_markup=main_keyboard())
        await state.clear()
        return
    if _user_reached_booking_limit(booking_user.id, date):
        await message.answer(
            f"В этом месяце можно оформить не более {BOOKINGS_PER_USER_PER_MONTH} записей. Попробуйте другой месяц.",
            reply_markup=main_keyboard(),
        )
        await state.clear()
        return

    create_booking(
        date,
        time_value,
        name,
        phone_value,
        booking_user.id,
        booking_user.username,
        duration_minutes=duration,
        service_ids=service_ids,
        service_names=service_names,
    )

    date_label = datetime.strptime(f"{date} {time_value}", "%Y-%m-%d %H:%M").strftime("%d.%m.%Y")
    await message.answer(
        f"✅ <b>Запись подтверждена!</b>\n\n"
        f"Услуги: <b>{escape(service_names)}</b>\n"
        f"⏱ Длительность: <b>{duration} мин.</b>\n"
        f"🗓 Дата: <b>{date_label}</b>\n"
        f"🕐 Время: <b>{time_value}</b>\n"
        f"👤 Имя: <b>{escape(name)}</b>\n"
        f"📞 Номер: <b>{escape(phone_value)}</b>",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

    if bot is None:
        bot = message.bot
    tg_line = f"📱 Telegram: @{escape(booking_user.username)}" if booking_user.username else f"📱 Telegram: без username (id: <code>{booking_user.id}</code>)"
    master_text = (
        f"🔔 <b>Новая запись!</b>\n\n"
        f"Услуги: <b>{escape(service_names)}</b>\n"
        f"⏱ Длительность: <b>{duration} мин.</b>\n"
        f"🗓 <b>{date_label}</b> в <b>{time_value}</b>\n"
        f"👤 {escape(name)}\n📞 {escape(phone_value)}\n{tg_line}"
    )
    for master_id in MASTER_IDS:
        try:
            await bot.send_message(master_id, master_text, parse_mode="HTML")
        except Exception:
            logger.exception("Failed to send new booking notification to master_id=%s for client_id=%s", master_id, booking_user.id)
    await state.clear()


@router.message(BookingFSM.entering_phone, F.contact)
async def phone_via_contact(message: Message, state: FSMContext):
    await _show_confirmation(message, state, message.contact.phone_number)


@router.message(BookingFSM.entering_phone, F.text)
async def phone_via_text(message: Message, state: FSMContext):
    phone = (message.text or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10:
        await message.answer("Пожалуйста, введите корректный номер, например +79001234567:")
        return
    await _show_confirmation(message, state, phone)


@router.callback_query(BookingFSM.confirming_booking, F.data == CONFIRM_BOOKING_CALLBACK)
async def confirm_booking(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await _safe_edit_text(call, "Запись подтверждается...")
    await _finish_booking(call.message, state, call.from_user)


@router.callback_query(BookingFSM.confirming_booking, F.data == EDIT_BOOKING_CALLBACK)
async def edit_booking(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    last_name = data.get("client_name") or get_last_client_name(call.from_user.id)
    await _safe_edit_text(call, "Введите имя заново:")
    await call.message.answer("Можно ввести новое имя или нажать кнопку с последним вариантом.", reply_markup=_name_keyboard(last_name))
    await call.message.answer("Если нужно, можно вернуться к выбору времени.", reply_markup=_back_to_time_keyboard())
    await state.set_state(BookingFSM.entering_name)


@router.callback_query(F.data.startswith("svc_cat:"))
@router.callback_query(F.data.startswith("svc_item:"))
@router.callback_query(F.data.startswith("date:"))
@router.callback_query(F.data.startswith("time:"))
@router.callback_query(F.data.startswith("busy:"))
async def stale_callback(call: CallbackQuery, state: FSMContext):
    await call.answer("Это меню уже устарело. Начните запись заново через кнопку «Запись».", show_alert=True)
    await state.clear()


@router.message()
async def debug_incoming_messages(message: Message):
    logger.info("Incoming message text=%r contact=%r", message.text, bool(message.contact))
