# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta, time
import pytz
import logging


def float_hours_to_hm(hours_float):
    h = int(hours_float)
    m = int(round((hours_float - h) * 60))
    return h, m

_logger = logging.getLogger(__name__)


class FsmChangeAppointmentWizard(models.TransientModel):
    """Wizard for changing appointment date/time for existing FSM tasks"""
    
    _name = 'fsm.change.appointment.wizard'
    _description = 'Change FSM Task Appointment'

    # Wizard state management
    state = fields.Selection(
        selection=[
            ("schedule", "Schedule"),
            ("notes", "Notes"),
            ("confirm", "Confirm"),
        ],
        default="schedule",
        string="Step",
        required=True,
    )

    # Reference to the task being rescheduled
    task_id = fields.Many2one('project.task', string='Task', required=True, readonly=True)
    task_name = fields.Char(related='task_id.name', string='Task Name', readonly=True)
    partner_id = fields.Many2one(related='task_id.partner_id', string='Customer', readonly=True)
    current_planned_date_begin = fields.Datetime(
        related='task_id.planned_date_begin',
        string='Current Start Time',
        readonly=True
    )

    buffer_before_mins = fields.Integer(related='task_id.fsm_task_type_id.buffer_before_mins', readonly=True)
    buffer_after_mins = fields.Integer(related='task_id.fsm_task_type_id.buffer_after_mins', readonly=True)

    # New scheduling fields
    planned_date_begin = fields.Datetime(string='New Start Time', help="Selected slot start time")
    planned_date_end = fields.Datetime(
        string='New End Time (Estimated)',
        compute='_compute_planned_date_end',
        store=True,
        readonly=True,
        help="Automatically calculated based on task's planned hours. You will be reminded to adjust this manually after saving."
    )
    
    # Duration - use a simple float field instead of related field
    planned_hours = fields.Float(
        string='Duration (Hours)',
        default=1.0,
        help="Estimated duration in hours"
    )

    # Team and slot selection
    team_id = fields.Many2one('fsm.team', string='Team', help="Optional team filter; slot search will prefer this team.")
    preferred_team_ids = fields.Many2many('fsm.team', compute='_compute_preferred_and_capable_teams', string='Preferred Teams', readonly=True)
    capable_only_team_ids = fields.Many2many('fsm.team', compute='_compute_preferred_and_capable_teams', string='Capable Teams', readonly=True)
    qualified_team_ids = fields.Many2many('fsm.team', compute='_compute_qualified_teams', string='Qualified Teams', readonly=True)
    slot_index = fields.Integer(default=0)
    slot1_label = fields.Char(compute='_compute_slots', store=True)
    slot2_label = fields.Char(compute='_compute_slots', store=True)
    slot3_label = fields.Char(compute='_compute_slots', store=True)
    slot1_start = fields.Datetime(compute='_compute_slots', store=True)
    slot2_start = fields.Datetime(compute='_compute_slots', store=True)
    slot3_start = fields.Datetime(compute='_compute_slots', store=True)
    slot1_end = fields.Datetime(compute='_compute_slots', store=True)
    slot2_end = fields.Datetime(compute='_compute_slots', store=True)
    slot3_end = fields.Datetime(compute='_compute_slots', store=True)
    slot1_team_id = fields.Many2one('fsm.team', compute='_compute_slots', readonly=True, store=True)
    slot2_team_id = fields.Many2one('fsm.team', compute='_compute_slots', readonly=True, store=True)
    slot3_team_id = fields.Many2one('fsm.team', compute='_compute_slots', readonly=True, store=True)
    slot1_team_label = fields.Char(compute='_compute_slots', readonly=True, store=True)
    slot2_team_label = fields.Char(compute='_compute_slots', readonly=True, store=True)
    slot3_team_label = fields.Char(compute='_compute_slots', readonly=True, store=True)
    slot1_is_preferred = fields.Boolean(compute='_compute_slots', readonly=True, store=True)
    slot2_is_preferred = fields.Boolean(compute='_compute_slots', readonly=True, store=True)
    slot3_is_preferred = fields.Boolean(compute='_compute_slots', readonly=True, store=True)
    search_start_dt = fields.Datetime(string='Slot Search Start', readonly=False)
    filter_use_date = fields.Boolean(string='Filter by Date')
    date_filter_start = fields.Date(string='Earliest Date')
    date_filter_end = fields.Date(string='Latest Date')
    filter_use_time = fields.Boolean(string='Filter by Time')
    time_filter_start = fields.Float(string='Earliest Time', help='Use HH:MM format', digits=(16, 2))
    time_filter_end = fields.Float(string='Latest Time', help='Use HH:MM format', digits=(16, 2))

    selected_slot = fields.Selection(selection='_get_slot_selection', default='1', string='Choose Appointment')
    selected_slot_label = fields.Char(compute='_compute_selected_slot_label', readonly=True, string='Selected Appointment')

    # Frozen selected slot data (captured when user selects slot, won't change when slots recompute)
    frozen_selected_start = fields.Datetime(string='Frozen Selected Start', readonly=True)
    frozen_selected_end = fields.Datetime(string='Frozen Selected End', readonly=True)
    frozen_selected_team_id = fields.Many2one('fsm.team', string='Frozen Selected Team', readonly=True)

    # Assignee fields
    user_ids = fields.Many2many(
        'res.users',
        string='Assign To',
        help="Select technicians to assign to this appointment"
    )
    current_user_ids = fields.Many2many(
        'res.users',
        relation='fsm_change_appointment_current_users_rel',
        related='task_id.user_ids',
        string='Current Assignees',
        readonly=True
    )

    # Notes
    notes = fields.Text(
        string="Reason for Change / Notes",
        help="Explain why the appointment is being rescheduled"
    )

    @api.onchange('team_id')
    def _onchange_team_id(self):
        self._compute_qualified_teams()
        self._compute_slots()

    @api.depends('planned_date_begin', 'planned_hours')
    def _compute_planned_date_end(self):
        """Calculate end time based on start time + planned hours"""
        for wizard in self:
            if wizard.planned_date_begin and wizard.planned_hours:
                wizard.planned_date_end = wizard.planned_date_begin + timedelta(hours=wizard.planned_hours)
            else:
                wizard.planned_date_end = False

    @api.onchange('planned_date_begin', 'planned_hours')
    def _onchange_planned_date_begin(self):
        """Update end date when start date or duration changes"""
        if self.planned_date_begin and self.planned_hours:
            self.planned_date_end = self.planned_date_begin + timedelta(hours=self.planned_hours)
        else:
            self.planned_date_end = False

    @api.depends('task_id')
    def _compute_preferred_and_capable_teams(self):
        for wiz in self:
            task_type = wiz.task_id.fsm_task_type_id if wiz.task_id else False
            preferred = task_type.preferred_team_ids if task_type else self.env['fsm.team']
            capable = task_type.capable_team_ids if task_type else self.env['fsm.team']
            wiz.preferred_team_ids = preferred
            wiz.capable_only_team_ids = capable - preferred if capable else self.env['fsm.team']

    @api.depends('task_id', 'team_id')
    def _compute_qualified_teams(self):
        for wiz in self:
            if wiz.team_id:
                wiz.qualified_team_ids = wiz.team_id
                continue
            task_type = wiz.task_id.fsm_task_type_id if wiz.task_id else False
            if not task_type:
                wiz.qualified_team_ids = self.env['fsm.team']
                continue
            preferred = task_type.preferred_team_ids or self.env['fsm.team']
            capable = task_type.capable_team_ids
            combined = (preferred | capable) if (preferred or capable) else self.env['fsm.team']
            wiz.qualified_team_ids = combined if combined else self.env['fsm.team'].search([('active', '=', True)])

    @api.depends('selected_slot', 'slot1_label', 'slot2_label', 'slot3_label')
    def _compute_selected_slot_label(self):
        for wiz in self:
            labels = {
                '1': wiz.slot1_label or _('No available slot'),
                '2': wiz.slot2_label or _('No available slot'),
                '3': wiz.slot3_label or _('No available slot'),
            }
            wiz.selected_slot_label = labels.get(wiz.selected_slot or '1', _('No available slot'))

    def _to_utc(self, dt):
        """Convert naive/local dt to UTC naive using user/context tz (default El Salvador)."""
        if not dt:
            return dt
        tz_name = self.env.context.get('tz') or self.env.user.tz or 'America/El_Salvador'
        tz = pytz.timezone(tz_name)
        local_dt = dt if dt.tzinfo else tz.localize(dt)
        return local_dt.astimezone(pytz.UTC).replace(tzinfo=None)

    def _round_to_nearest_10(self, dt):
        """Round datetime to the nearest 10-minute mark."""
        if not dt:
            return dt
        remainder = dt.minute % 10
        minute = dt.minute - remainder + (10 if remainder >= 5 else 0)
        if minute == 60:
            dt = dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            dt = dt.replace(minute=minute, second=0, microsecond=0)
        return dt

    def _get_duration_hours(self):
        hours = self.planned_hours
        if not hours and self.task_id and hasattr(self.task_id, 'planned_hours'):
            hours = self.task_id.planned_hours
        return max(hours or 0.0, 1.0)

    def _build_end_time_warning_effect(self, end_dt_utc):
        """Return a reminder effect so agents adjust the task end to the booking end."""
        self.ensure_one()
        if not end_dt_utc:
            return None
        end_local = fields.Datetime.context_timestamp(self, end_dt_utc)
        end_label = end_local.strftime("%Y-%m-%d %H:%M") if end_local else ""
        return {
            "fadeout": "slow",
            "message": _("Before saving this task, change the end date and time to %s.") % end_label,
            "type": "rainbow_man",
        }

    def _get_slot_label_map(self):
        self.ensure_one()
        return {
            '1': self.slot1_label or _('No available slot'),
            '2': self.slot2_label or _('No available slot'),
            '3': self.slot3_label or _('No available slot'),
        }

    @api.model
    def _get_slot_selection(self):
        labels = self.env.context.get('slot_labels') or {
            '1': _('Option 1'),
            '2': _('Option 2'),
            '3': _('Option 3'),
        }
        return [(key, labels.get(key) or _('Option %s') % key) for key in ['1', '2', '3']]

    def _find_top_slots(self, start_dt, limit=3, date_end=None, time_start=None, time_end=None):
        """
        Return a list of top available slots sorted by start time.
        Each slot is a dict: {"start": datetime, "end": datetime, "team": fsm.team}.
        Availability uses team calendars and avoids overlaps with bookings/tasks sharing the same lead.
        """
        self.ensure_one()
        needed_hours = self._get_duration_hours()
        buffer_before = timedelta(minutes=(self.buffer_before_mins or 0))
        buffer_after = timedelta(minutes=(self.buffer_after_mins or 0))

        if self.team_id:
            teams = self.team_id
        else:
            teams = self.qualified_team_ids
        if not teams:
            teams = self.env['fsm.team'].search([('active', '=', True)])

        slots = []
        search_end = date_end or (start_dt + timedelta(days=14))
        search_start_utc = self._to_utc(start_dt)
        search_end_utc = self._to_utc(search_end)
        lead_minutes = int(self.env['ir.config_parameter'].sudo().get_param('fsm_guided_intake.slot_start_lead_minutes', '0') or 0)

        lead_to_team_ids = {}
        leads = teams.mapped('lead_user_id').filtered(lambda u: u)
        if leads:
            all_lead_teams = self.env['fsm.team'].search([('lead_user_id', 'in', leads.ids)])
            for lead in leads:
                lead_to_team_ids[lead.id] = all_lead_teams.filtered(lambda t: t.lead_user_id.id == lead.id).ids

        for team in teams:
            calendar = (
                team.calendar_id
                or getattr(team.lead_user_id, 'resource_calendar_id', False)
                or self.env.company.resource_calendar_id
                or self.env.ref('resource.resource_calendar_std', raise_if_not=False)
            )
            if not calendar:
                continue
            attendances = calendar.attendance_ids.filtered(lambda a: not a.display_type)
            if not attendances:
                continue

            team_ids_for_lead = lead_to_team_ids.get(team.lead_user_id.id, [team.id])
            existing_bookings = self.env['fsm.booking'].search([
                ('team_id', 'in', team_ids_for_lead),
                ('state', '!=', 'cancelled'),
                ('start_datetime', '<', search_end_utc),
                ('end_datetime', '>', search_start_utc),
            ])
            existing_bookings = existing_bookings.filtered(lambda b: b.task_id.id != self.task_id.id)

            task_intervals = []
            Task = self.env['project.task']
            if 'team_id' in Task._fields:
                task_domain = [('team_id', 'in', team_ids_for_lead), ('stage_id.fold', '=', False)]
                if 'planned_date_begin' in Task._fields and 'planned_date_end' in Task._fields:
                    task_domain += [
                        ('planned_date_begin', '<', search_end_utc),
                        ('planned_date_end', '>', search_start_utc),
                    ]
                elif 'date_start' in Task._fields and 'date_end' in Task._fields:
                    task_domain += [
                        ('date_start', '<', search_end_utc),
                        ('date_end', '>', search_start_utc),
                    ]
                tasks = Task.search(task_domain)
                for t in tasks:
                    start = getattr(t, 'planned_date_begin', False) or getattr(t, 'date_start', False)
                    end = getattr(t, 'planned_date_end', False) or getattr(t, 'date_end', False)
                    if start and end and t.id != self.task_id.id:
                        task_intervals.append((start, end))

            current_day = start_dt.date()
            while datetime.combine(current_day, time.min) < search_end:
                if self.filter_use_date and self.date_filter_start and current_day < self.date_filter_start:
                    current_day += timedelta(days=1)
                    continue
                if self.filter_use_date and self.date_filter_end and current_day > self.date_filter_end:
                    break
                weekday_str = str(current_day.weekday())
                day_attendances = attendances.filtered(lambda a: a.dayofweek == weekday_str)
                if day_attendances:
                    earliest = min(day_attendances.mapped('hour_from'))
                    latest = max(day_attendances.mapped('hour_to'))

                    effective_start = earliest
                    effective_end = latest
                    if time_start is not None:
                        effective_start = max(effective_start, time_start)
                    if time_end is not None:
                        effective_end = min(effective_end, time_end)
                    start_hour, start_min = float_hours_to_hm(effective_start)
                    end_candidate = effective_end
                    end_hour, end_min = float_hours_to_hm(end_candidate)
                    shift_start_dt = datetime.combine(current_day, time(start_hour, start_min)) + timedelta(minutes=lead_minutes)
                    shift_end_dt = datetime.combine(current_day, time(end_hour, end_min)) + timedelta(hours=1)

                    if shift_end_dt <= shift_start_dt:
                        current_day += timedelta(days=1)
                        continue

                    if shift_start_dt < start_dt:
                        shift_start_dt = start_dt

                    cursor = shift_start_dt
                    step = timedelta(minutes=30)
                    while cursor + timedelta(hours=needed_hours) + buffer_before + buffer_after <= shift_end_dt:
                        slot_start = cursor + buffer_before
                        slot_end = slot_start + timedelta(hours=needed_hours) + buffer_after

                        slot_start_utc = self._to_utc(slot_start)
                        slot_end_utc = self._to_utc(slot_end)
                        overlap = existing_bookings.filtered(
                            lambda b: b.start_datetime < slot_end_utc and b.end_datetime > slot_start_utc
                        )
                        if not overlap and task_intervals:
                            for start_dt_val, end_dt_val in task_intervals:
                                if start_dt_val < slot_end_utc and end_dt_val > slot_start_utc:
                                    overlap = True
                                    break

                        tz_name = self.env.context.get('tz') or self.env.user.tz or 'America/El_Salvador'
                        tz = pytz.timezone(tz_name)
                        now_utc = fields.Datetime.now()
                        slot_start_tz = pytz.UTC.localize(slot_start_utc).astimezone(tz)
                        now_tz = pytz.UTC.localize(now_utc).astimezone(tz)

                        if slot_start_tz.date() == now_tz.date():
                            if slot_start_tz >= now_tz:
                                if not overlap:
                                    slots.append({'start': slot_start, 'end': slot_end, 'team': team})
                        else:
                            if not overlap:
                                slots.append({'start': slot_start, 'end': slot_end, 'team': team})
                        cursor += step

                current_day += timedelta(days=1)

        slots.sort(key=lambda s: s['start'])
        return slots[:limit]

    @api.depends('task_id', 'partner_id', 'planned_hours', 'slot_index', 'search_start_dt', 'date_filter_start', 'date_filter_end', 'time_filter_start', 'time_filter_end', 'filter_use_date', 'filter_use_time', 'team_id')
    def _compute_slots(self):
        for wiz in self:
            wiz.slot1_label = False
            wiz.slot2_label = False
            wiz.slot3_label = False
            wiz.slot1_start = False
            wiz.slot2_start = False
            wiz.slot3_start = False
            wiz.slot1_end = False
            wiz.slot2_end = False
            wiz.slot3_end = False
            wiz.slot1_team_id = False
            wiz.slot2_team_id = False
            wiz.slot3_team_id = False
            wiz.slot1_team_label = False
            wiz.slot2_team_label = False
            wiz.slot3_team_label = False
            wiz.slot1_is_preferred = False
            wiz.slot2_is_preferred = False
            wiz.slot3_is_preferred = False

            if not wiz.task_id or not wiz.partner_id:
                continue
            if (wiz.planned_hours or 0.0) <= 0:
                continue

            start_dt = wiz.search_start_dt or (fields.Datetime.now() + timedelta(minutes=15))
            if wiz.filter_use_date and wiz.date_filter_start:
                start_dt = datetime.combine(wiz.date_filter_start, time.min)
            search_end = datetime.combine(wiz.date_filter_end, time.max) if (wiz.filter_use_date and wiz.date_filter_end) else None

            slots = []
            chosen_start = start_dt
            max_attempts = 84
            for attempt in range(max_attempts):
                start_dt_attempt = start_dt + timedelta(hours=attempt * 2.0)
                start_dt_attempt = wiz._round_to_nearest_10(start_dt_attempt)
                slots = wiz._find_top_slots(
                    start_dt_attempt,
                    limit=3,
                    date_end=search_end,
                    time_start=wiz.time_filter_start if wiz.filter_use_time else None,
                    time_end=wiz.time_filter_end if wiz.filter_use_time else None,
                )
                uniq = []
                seen = set()
                for s in slots:
                    key = (s['team'].id if s['team'] else False, s['start'], s['end'])
                    if key in seen:
                        continue
                    seen.add(key)
                    uniq.append(s)
                slots = uniq
                if slots:
                    chosen_start = start_dt_attempt
                    break

            wiz.search_start_dt = chosen_start

            uniq_slots = []
            seen_keys = set()
            for s in slots:
                key = (s['team'].id if s.get('team') else False, s.get('start'), s.get('end'))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                uniq_slots.append(s)
            slots = uniq_slots

            if len(slots) > 0:
                wiz.slot1_start = slots[0]['start']
                wiz.slot1_end = slots[0]['end']
                wiz.slot1_team_id = slots[0]['team']
                wiz.slot1_team_label = slots[0]['team'].lead_user_id.name or slots[0]['team'].name
                wiz.slot1_is_preferred = slots[0]['team'] in wiz.preferred_team_ids
                wiz.slot1_label = _("%s, %s - %s") % (
                    slots[0]['start'].strftime("%a, %B %d"),
                    slots[0]['start'].strftime("%H:%M"),
                    slots[0]['end'].strftime("%H:%M"),
                )
            if len(slots) > 1:
                wiz.slot2_start = slots[1]['start']
                wiz.slot2_end = slots[1]['end']
                wiz.slot2_team_id = slots[1]['team']
                wiz.slot2_team_label = slots[1]['team'].lead_user_id.name or slots[1]['team'].name
                wiz.slot2_is_preferred = slots[1]['team'] in wiz.preferred_team_ids
                wiz.slot2_label = _("%s, %s - %s") % (
                    slots[1]['start'].strftime("%a, %B %d"),
                    slots[1]['start'].strftime("%H:%M"),
                    slots[1]['end'].strftime("%H:%M"),
                )
            if len(slots) > 2:
                wiz.slot3_start = slots[2]['start']
                wiz.slot3_end = slots[2]['end']
                wiz.slot3_team_id = slots[2]['team']
                wiz.slot3_team_label = slots[2]['team'].lead_user_id.name or slots[2]['team'].name
                wiz.slot3_is_preferred = slots[2]['team'] in wiz.preferred_team_ids
                wiz.slot3_label = _("%s, %s - %s") % (
                    slots[2]['start'].strftime("%a, %B %d"),
                    slots[2]['start'].strftime("%H:%M"),
                    slots[2]['end'].strftime("%H:%M"),
                )

            last_end = wiz.slot3_end or wiz.slot1_end or wiz.search_start_dt or fields.Datetime.now()
            if last_end:
                wiz.search_start_dt = last_end + timedelta(hours=2.0)

    @api.onchange('selected_slot')
    def _onchange_selected_slot(self):
        """Capture and freeze selected slot data so it doesn't change when slots recompute"""
        if not self.selected_slot:
            return
            
        slot_map = {
            '1': (self.slot1_start, self.slot1_end, self.slot1_team_id),
            '2': (self.slot2_start, self.slot2_end, self.slot2_team_id),
            '3': (self.slot3_start, self.slot3_end, self.slot3_team_id),
        }
        start_dt, end_dt, team_id = slot_map.get(self.selected_slot, (self.slot1_start, self.slot1_end, self.slot1_team_id))
        
        # CRITICAL: Persist frozen values immediately to database
        # This prevents them from being lost when slots recompute
        if self.id:
            self.write({
                'frozen_selected_start': start_dt,
                'frozen_selected_end': end_dt,
                'frozen_selected_team_id': team_id.id if team_id else False,
            })
            _logger.info(f"[ONCHANGE] Frozen slot {self.selected_slot} -> {start_dt} to {end_dt}")
        else:
            # Wizard not yet saved, set in-memory
            self.frozen_selected_start = start_dt
            self.frozen_selected_end = end_dt
            self.frozen_selected_team_id = team_id
        
        # Update planned dates
        if start_dt:
            self.planned_date_begin = start_dt
            duration = self._get_duration_hours()
            self.planned_date_end = start_dt + timedelta(hours=duration)

    @api.model
    def default_get(self, fields_list):
        """Set default values when wizard is opened"""
        res = super(FsmChangeAppointmentWizard, self).default_get(fields_list)
        
        # Get the active task from context
        active_id = self.env.context.get('active_id')
        if active_id:
            task = self.env['project.task'].browse(active_id)
            res['task_id'] = task.id
            
            # Default new start time to current start time (or now if not set)
            if task.planned_date_begin:
                res['planned_date_begin'] = task.planned_date_begin
            else:
                res['planned_date_begin'] = fields.Datetime.now()
            
            # Get planned_hours if field exists on task
            if hasattr(task, 'planned_hours') and task.planned_hours:
                res['planned_hours'] = task.planned_hours
            else:
                res['planned_hours'] = task.fsm_default_planned_hours or 1.0 if hasattr(task, 'fsm_default_planned_hours') else 1.0
            res['search_start_dt'] = task.planned_date_begin or fields.Datetime.now()
            if task.fsm_booking_id:
                res['team_id'] = task.fsm_booking_id.team_id.id
            elif hasattr(task, 'team_id'):
                res['team_id'] = getattr(task, 'team_id').id if getattr(task, 'team_id') else False
            
            # Default assignees to current assignees
            if task.user_ids:
                res['user_ids'] = [(6, 0, task.user_ids.ids)]
        
        return res

    def _get_wizard_title(self):
        """Return the wizard title based on current state"""
        self.ensure_one()
        titles = {
            "schedule": _("Change Appointment - Select Date/Time"),
            "notes": _("Change Appointment - Add Notes"),
            "confirm": _("Change Appointment - Confirm Changes"),
        }
        return titles.get(self.state, _("Change Appointment"))

    def action_next(self):
        """Move to the next wizard step"""
        self.ensure_one()
        
        # Validate current step
        if self.state == "schedule" and not (self.slot1_start or self.slot2_start or self.slot3_start):
            raise UserError(_("No available appointment slots were found."))
        
        # CRITICAL: Capture slot data NOW before state transition
        # This data will be passed via context and won't be lost when slots recompute
        ctx = dict(self.env.context, slot_labels=self._get_slot_label_map(), search_start_dt=self.search_start_dt)
        
        if self.state == "schedule" and self.selected_slot:
            # Get the actual slot data from current computed values
            slot_map = {
                '1': (self.slot1_start, self.slot1_end, self.slot1_team_id),
                '2': (self.slot2_start, self.slot2_end, self.slot2_team_id),
                '3': (self.slot3_start, self.slot3_end, self.slot3_team_id),
            }
            start_dt, end_dt, team_id = slot_map.get(self.selected_slot, (self.slot1_start, self.slot1_end, self.slot1_team_id))
            
            # Store in context so it survives the form reload
            ctx.update({
                'frozen_slot_start': start_dt.isoformat() if start_dt else False,
                'frozen_slot_end': end_dt.isoformat() if end_dt else False,
                'frozen_slot_team_id': team_id.id if team_id else False,
                'frozen_slot_number': self.selected_slot,
            })
            
            # Also persist to DB fields as backup
            self.sudo().write({
                'frozen_selected_start': start_dt,
                'frozen_selected_end': end_dt,
                'frozen_selected_team_id': team_id.id if team_id else False,
            })
            _logger.info(f"[ACTION_NEXT] Captured slot {self.selected_slot}: {start_dt} to {end_dt} (team: {team_id.name if team_id else 'None'})")
        
        # Determine next state
        order = ["schedule", "notes", "confirm"]
        idx = order.index(self.state)
        
        if self.state == "confirm":
            return {"type": "ir.actions.act_window_close"}
        
        self.state = order[min(idx + 1, len(order) - 1)]
        
        return {
            "type": "ir.actions.act_window",
            "res_model": "fsm.change.appointment.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
            "name": self._get_wizard_title(),
            "context": ctx,
        }

    def action_back(self):
        """Move to the previous wizard step"""
        self.ensure_one()
        
        order = ["schedule", "notes", "confirm"]
        idx = order.index(self.state)
        self.state = order[max(idx - 1, 0)]
        
        return {
            "type": "ir.actions.act_window",
            "res_model": "fsm.change.appointment.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
            "name": self._get_wizard_title(),
            "context": dict(self.env.context, slot_labels=self._get_slot_label_map(), search_start_dt=self.search_start_dt),
        }

    def action_more_options(self):
        self.ensure_one()
        base = self.slot3_end or self.slot1_end or fields.Datetime.now()
        self.search_start_dt = (base or fields.Datetime.now()) + timedelta(hours=2.0)
        return {
            "type": "ir.actions.act_window",
            "res_model": "fsm.change.appointment.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
            "name": self._get_wizard_title(),
            "context": dict(self.env.context, slot_labels=self._get_slot_label_map(), search_start_dt=self.search_start_dt),
        }

    def action_confirm_change(self):
        """Archive the old task and create a new one with the rescheduled appointment"""
        self.ensure_one()
        
        if not self.task_id:
            raise UserError(_("No task found to update."))
        
        # Try to get slot data from context FIRST (most reliable)
        # Context values are passed from action_next() and survive form reload
        ctx = self.env.context
        start_dt = None
        end_dt = None
        slot_team = None
        
        if ctx.get('frozen_slot_start'):
            # Parse ISO datetime from context
            from dateutil import parser
            start_dt = parser.parse(ctx['frozen_slot_start'])
            end_dt = parser.parse(ctx['frozen_slot_end']) if ctx.get('frozen_slot_end') else None
            if ctx.get('frozen_slot_team_id'):
                slot_team = self.env['fsm.team'].browse(ctx['frozen_slot_team_id'])
            _logger.info(f"[CONFIRM] Using slot data from CONTEXT: {start_dt} to {end_dt}")
        else:
            # Fallback to frozen database fields
            start_dt = self.frozen_selected_start
            end_dt = self.frozen_selected_end
            slot_team = self.frozen_selected_team_id
            _logger.info(f"[CONFIRM] Using slot data from FROZEN FIELDS: {start_dt} to {end_dt}")
        
        if not start_dt:
            raise UserError(_("Please pick an available appointment slot."))

        # Calculate end date based on duration
        duration_hours = self._get_duration_hours()
        end_dt = start_dt + timedelta(hours=duration_hours)

        # Determine team
        team = slot_team or self.team_id
        if not team and self.task_id.fsm_booking_id:
            team = self.task_id.fsm_booking_id.team_id
        if not team:
            team = self.env['fsm.team'].search([], limit=1)

        # Convert to UTC
        start_dt_utc = self._to_utc(start_dt)
        end_dt_utc = self._to_utc(end_dt)

        # Prepare assignees
        assignee_user_ids = []
        if self.user_ids:
            assignee_user_ids = self.user_ids.ids
        elif team:
            if team.lead_user_id:
                assignee_user_ids.append(team.lead_user_id.id)
            member_users = team.member_ids.mapped('user_id').filtered(lambda u: u)
            assignee_user_ids += member_users.ids
        elif self.task_id.user_ids:
            assignee_user_ids = self.task_id.user_ids.ids
        assignee_user_ids = list(dict.fromkeys(assignee_user_ids))

        new_task = self.task_id.reschedule_clone_to_new_task(
            start_dt_utc=start_dt_utc,
            end_dt_utc=end_dt_utc,
            team=team,
            duration_hours=duration_hours,
            notes=self.notes,
            assignee_user_ids=assignee_user_ids,
        )

        _logger.info(f"Task {self.task_id.id} archived. New task created with ID: {new_task.id}")

        # Open the new task
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Rescheduled Appointment'),
            'res_model': 'project.task',
            'res_id': new_task.id,
            'view_mode': 'form',
            'view_type': 'form',
            'target': 'current',
        }
        warning_effect = self._build_end_time_warning_effect(end_dt_utc)
        if warning_effect:
            action['effect'] = warning_effect
        return action

    @api.model
    def fields_view_get(self, view_id=None, view_type="form", toolbar=False, submenu=False):
        res = super().fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)
        if view_type != 'form':
            return res

        res_id = self.env.context.get('res_id')
        if not res_id or 'selected_slot' not in res.get('fields', {}):
            return res

        wiz = self.browse(res_id)
        labels = wiz._get_slot_label_map()
        selection = [(key, labels.get(key) or _('No available slot')) for key in ['1', '2', '3']]
        res['fields']['selected_slot']['selection'] = selection
        return res
