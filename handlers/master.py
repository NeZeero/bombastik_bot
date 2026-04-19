from __future__ import annotations

import logging
from datetime import datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import MASTER_CONTACT, is_master
from database import (
    add_work_ranges,
    cancel_booking,
    delete_slot,
    get_all_bookings,
    get_booked_dates,
    get_bookings_for_date,
    get_slots_for_date_full,
    get_work_ranges_for_date,
    parse_work_ranges,
)
from time_utils import today_local

router = Router()
logger = logging.getLogger(__name__)

MASTER_PAGE_SIZE = 30
MASTER_CALENDAR_DAYS = 60


class MasterFSM(StatesGroup):
    waiting_times = State()


def _master_panel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Добавить рабочие дни", callback_data="m_add_days")],
            [InlineKeyboardButton(text="🗑 Удалить день/слот", callback_data="m_del_slot")],
            [InlineKeyboardButton(text="❌ Отмена записи", callback_data="m_cancel")],
            [InlineKeyboardButton(text="📋 Все записи", callback_data="m_bookings")],
        ]
    )


def _future_dates(days: int = MASTER_CALENDAR_DAYS) -> list[str]:
    today = today_local()
    return [(today + timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(days)]


def _paginate_dates_keyboard(dates: list[str], *, page: int, item_prefix: str, nav_prefix: str, back_callback: str = "m_back", per_row: int = 5, label_format: str = "%d.%m") -> InlineKeyboardMarkup:
    safe_page = max(page, 0)
    start = safe_page * MASTER_PAGE_SIZE
    page_dates = dates[start:start + MASTER_PAGE_SIZE]
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for date_str in page_dates:
        row.append(InlineKeyboardButton(text=datetime.strptime(date_str, "%Y-%m-%d").strftime(label_format), callback_data=f"{item_prefix}:{date_str}"))
        if len(row) >= per_row:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    nav_row: list[InlineKeyboardButton] = []
    if safe_page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"{nav_prefix}:{safe_page - 1}"))
    if start + MASTER_PAGE_SIZE < len(dates):
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"{nav_prefix}:{safe_page + 1}"))
    if nav_row:
        buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _calendar_keyboard(*, page: int, item_prefix: str, nav_prefix: str, back_callback: str = "m_back") -> InlineKeyboardMarkup:
    return _paginate_dates_keyboard(_future_dates(), page=page, item_prefix=item_prefix, nav_prefix=nav_prefix, back_callback=back_callback)


def _master_times_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к дням", callback_data="m_times_back_days")],
            [InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_times_back_panel")],
        ]
    )


@router.message(Command("master"))
async def master_panel(message: Message):
    if not is_master(message.from_user.id):
        return
    await message.answer("👩‍💼 <b>Панель мастера</b>\n\nВыберите действие:", parse_mode="HTML", reply_markup=_master_panel_markup())


@router.callback_query(F.data == "m_add_days")
async def add_days_start(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    await call.message.edit_text(
        "📅 Нажмите на день, чтобы добавить рабочий интервал:\n\nНапример: <code>09:00-16:00 17:00-20:00</code>",
        parse_mode="HTML",
        reply_markup=_calendar_keyboard(page=0, item_prefix="mday", nav_prefix="mdaypage"),
    )


@router.callback_query(F.data.startswith("mdaypage:"))
async def add_days_paginate(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    page = int(call.data.split(":", 1)[1]) if call.data.split(":", 1)[1].isdigit() else 0
    await call.message.edit_text(
        "📅 Нажмите на день, чтобы добавить рабочий интервал:\n\nНапример: <code>09:00-16:00 17:00-20:00</code>",
        parse_mode="HTML",
        reply_markup=_calendar_keyboard(page=page, item_prefix="mday", nav_prefix="mdaypage"),
    )


@router.callback_query(F.data.startswith("mday:"))
async def add_day_times(call: CallbackQuery, state: FSMContext):
    if not is_master(call.from_user.id):
        return
    date = call.data.split(":", 1)[1]
    await state.update_data(master_date=date)
    existing_ranges = get_work_ranges_for_date(date)
    if existing_ranges:
        ranges_text = "Текущие интервалы:\n" + " ".join(existing_ranges)
    else:
        ranges_text = "Текущих интервалов пока нет."
    await call.message.edit_text(
        f"🕐 Введите рабочие интервалы для <b>{datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m.%Y')}</b>\n\n"
        f"{ranges_text}\n\n"
        "Можно указать несколько интервалов через пробел.\n"
        "Пример: <code>09:00-16:00 17:00-20:00</code>\n\n"
        "Внутри интервалов запись будет доступна каждые 15 минут.",
        parse_mode="HTML",
        reply_markup=_master_times_back_keyboard(),
    )
    await state.set_state(MasterFSM.waiting_times)


@router.callback_query(MasterFSM.waiting_times, F.data == "m_times_back_days")
async def master_times_back_days(call: CallbackQuery, state: FSMContext):
    if not is_master(call.from_user.id):
        return
    await state.clear()
    await call.message.edit_text(
        "📅 Нажмите на день, чтобы добавить рабочий интервал:",
        reply_markup=_calendar_keyboard(page=0, item_prefix="mday", nav_prefix="mdaypage"),
    )


@router.callback_query(MasterFSM.waiting_times, F.data == "m_times_back_panel")
async def master_times_back_panel(call: CallbackQuery, state: FSMContext):
    if not is_master(call.from_user.id):
        return
    await state.clear()
    await call.message.edit_text("👩‍💼 <b>Панель мастера</b>\n\nВыберите действие:", parse_mode="HTML", reply_markup=_master_panel_markup())


@router.message(MasterFSM.waiting_times)
async def save_times(message: Message, state: FSMContext):
    if not is_master(message.from_user.id):
        return
    data = await state.get_data()
    date = data.get("master_date")
    try:
        ranges = parse_work_ranges(message.text or "")
    except ValueError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}\nПример: <code>09:00-16:00 17:00-20:00</code>", parse_mode="HTML", reply_markup=_master_times_back_keyboard())
        return

    added, skipped = add_work_ranges(date, ranges)
    date_label = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
    msg = f"✅ Обновлено расписание на <b>{date_label}</b>."
    if added:
        msg += f"\n\nДобавлено стартов записи: {len(added)}"
    if skipped:
        msg += f"\nУже были в расписании: {len(skipped)}"
    await message.answer(msg, parse_mode="HTML")
    await state.clear()
    await message.answer("📅 Выберите следующий день:", reply_markup=_calendar_keyboard(page=0, item_prefix="mday", nav_prefix="mdaypage"))


@router.callback_query(F.data == "m_del_slot")
async def del_slot_start(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    await call.message.edit_text("Выберите день для удаления слотов:", reply_markup=_calendar_keyboard(page=0, item_prefix="mdelday", nav_prefix="mdelpage"))


@router.callback_query(F.data.startswith("mdelpage:"))
async def del_slot_paginate(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    page = int(call.data.split(":", 1)[1]) if call.data.split(":", 1)[1].isdigit() else 0
    await call.message.edit_text("Выберите день для удаления слотов:", reply_markup=_calendar_keyboard(page=page, item_prefix="mdelday", nav_prefix="mdelpage"))


@router.callback_query(F.data.startswith("mdelday:"))
async def del_day_slots(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    date = call.data.split(":", 1)[1]
    slots = get_slots_for_date_full(date)
    if not slots:
        await call.answer("На этот день нет слотов.", show_alert=True)
        return
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for time, is_booked in slots:
        status = "🔒" if is_booked else "🗑"
        callback_data = "noop" if is_booked else f"mdelslot:{date}:{time}"
        row.append(InlineKeyboardButton(text=f"{status} {time}", callback_data=callback_data))
        if len(row) >= 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Удалить все слоты", callback_data=f"mdelslotsall:{date}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к дням", callback_data="m_del_slot")])
    buttons.append([InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_back")])
    await call.message.edit_text(f"🗑 Слоты на <b>{datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m.%Y')}</b>:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("mdelslot:"))
async def confirm_del_slot(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    _, date, time = call.data.split(":", 2)
    delete_slot(date, time)
    await call.answer(f"✅ Слот {time} удалён", show_alert=True)
    slots = get_slots_for_date_full(date)
    if not slots:
        await call.message.edit_text(
            "На этом дне больше не осталось слотов.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад к дням", callback_data="m_del_slot")],
                    [InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_back")],
                ]
            ),
        )
        return

    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for slot_time, is_booked in slots:
        status = "🔒" if is_booked else "🗑"
        callback_data = "noop" if is_booked else f"mdelslot:{date}:{slot_time}"
        row.append(InlineKeyboardButton(text=f"{status} {slot_time}", callback_data=callback_data))
        if len(row) >= 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Удалить все слоты", callback_data=f"mdelslotsall:{date}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к дням", callback_data="m_del_slot")])
    buttons.append([InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_back")])
    await call.message.edit_text(
        f"🗑 Слоты на <b>{datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m.%Y')}</b>:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("mdelslotsall:"))
async def delete_all_slots_for_day(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    _, date = call.data.split(":", 1)
    slots = get_slots_for_date_full(date)
    if not slots:
        await call.answer("На этот день уже нет слотов.", show_alert=True)
        return
    for slot_time, is_booked in slots:
        if is_booked:
            continue
        delete_slot(date, slot_time)
    await call.answer("❌ Все свободные слоты удалены", show_alert=True)
    remaining = get_slots_for_date_full(date)
    if not remaining:
        await call.message.edit_text(
            "На этом дне больше не осталось слотов.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад к дням", callback_data="m_del_slot")],
                    [InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_back")],
                ]
            ),
        )
        return
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for slot_time, is_booked in remaining:
        status = "🔒" if is_booked else "🗑"
        callback_data = "noop" if is_booked else f"mdelslot:{date}:{slot_time}"
        row.append(InlineKeyboardButton(text=f"{status} {slot_time}", callback_data=callback_data))
        if len(row) >= 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Удалить все слоты", callback_data=f"mdelslotsall:{date}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к дням", callback_data="m_del_slot")])
    buttons.append([InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_back")])
    await call.message.edit_text(
        f"🗑 Слоты на <b>{datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m.%Y')}</b>:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer("Этот слот занят, удалить его нельзя.", show_alert=True)


@router.callback_query(F.data == "m_cancel")
async def cancel_start(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    dates = get_booked_dates()
    if not dates:
        await call.message.edit_text("❌ Сейчас нет активных записей.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="m_back")]]))
        return
    await call.message.edit_text("❌ Выберите дату, чтобы отменить запись:", reply_markup=_paginate_dates_keyboard(dates, page=0, item_prefix="mcday", nav_prefix="mcpage", per_row=3, label_format="%d.%m.%Y"))


@router.callback_query(F.data.startswith("mcpage:"))
async def cancel_paginate(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    dates = get_booked_dates()
    page = int(call.data.split(":", 1)[1]) if call.data.split(":", 1)[1].isdigit() else 0
    await call.message.edit_text("❌ Выберите дату, чтобы отменить запись:", reply_markup=_paginate_dates_keyboard(dates, page=page, item_prefix="mcday", nav_prefix="mcpage", per_row=3, label_format="%d.%m.%Y"))


@router.callback_query(F.data.startswith("mcday:"))
async def cancel_pick_day(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    date = call.data.split(":", 1)[1]
    rows = get_bookings_for_date(date)
    if not rows:
        await call.answer("На этот день уже нет записей.", show_alert=True)
        return
    buttons = []
    for booking_id, time, name, phone, client_id, service_names, duration in rows:
        label = f"❌ {time} — {name} ({duration} мин.)"
        if service_names:
            label = f"❌ {time} — {name}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"mcancel:{booking_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к датам", callback_data="m_cancel")])
    buttons.append([InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_back")])
    await call.message.edit_text(f"❌ Записи на <b>{datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m.%Y')}</b>:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("mcancel:"))
async def cancel_do(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    booking_id = int(call.data.split(":", 1)[1])
    row = cancel_booking(booking_id)
    if not row:
        await call.answer("Эта запись уже отменена.", show_alert=True)
        return
    date, time, name, phone, client_id = row
    await call.answer("✅ Запись отменена", show_alert=True)
    await call.message.edit_text(
        f"✅ Отменено: <b>{datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m.%Y')}</b> {time} — {escape(name)} ({escape(phone)})",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Ещё отмена", callback_data="m_cancel")], [InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_back")]]),
    )
    if client_id:
        try:
            await call.bot.send_message(client_id, "❌ Ваша запись была отменена.\nДля повторной записи обратитесь к боту.\n" + f"По вопросам: {MASTER_CONTACT}")
        except Exception:
            logger.exception("Failed to send cancellation notice for booking_id=%s", booking_id)


@router.callback_query(F.data == "m_bookings")
async def show_bookings(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    rows = get_all_bookings()
    if not rows:
        await call.message.edit_text("📋 <b>Все записи:</b>\n\nПредстоящих записей нет.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="m_back")]]))
        return
    lines = ["📋 <b>Предстоящие записи:</b>\n"]
    for date, time, name, phone, client_id, client_username, service_names, duration in rows:
        date_label = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
        tg_line = f"📱 Telegram: @{escape(client_username)}" if client_username else (f"📱 Telegram: без username (id: <code>{client_id}</code>)" if client_id else "📱 Telegram: —")
        services_line = f"\n  Услуги: {escape(service_names)}" if service_names else ""
        lines.append(f"• {date_label} {time} — {escape(name)} | {escape(phone)}\n  Длительность: {duration} мин.{services_line}\n  {tg_line}")
    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="m_back")]]))


@router.callback_query(F.data == "m_back")
async def master_back(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    await call.message.edit_text("👩‍💼 <b>Панель мастера</b>\n\nВыберите действие:", parse_mode="HTML", reply_markup=_master_panel_markup())
