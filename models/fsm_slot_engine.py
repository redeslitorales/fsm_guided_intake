# models/fsm_slot_engine.py
# -*- coding: utf-8 -*-

from odoo import api, fields, models
from datetime import datetime, timedelta, time
import pytz
import logging  
_logger = logging.getLogger(__name__)


def _merge_intervals(intervals):
    """intervals: list[(start_dt, end_dt)] sorted or unsorted; returns merged list sorted."""
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: x[0])
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged

def _subtract_intervals(window_start, window_end, busy):
    """Return open segments inside [window_start, window_end) after subtracting busy intervals."""
    if window_end <= window_start:
        return []
    busy = _merge_intervals([b for b in busy if b[1] > window_start and b[0] < window_end])
    open_segments = []
    cursor = window_start
    for b_start, b_end in busy:
        b_start = max(b_start, window_start)
        b_end = min(b_end, window_end)
        if b_start > cursor:
            open_segments.append((cursor, b_start))
        cursor = max(cursor, b_end)
    if cursor < window_end:
        open_segments.append((cursor, window_end))
    return open_segments


class FsmSlotEngine(models.AbstractModel):
    _name = "fsm.slot.engine"
    _description = "FSM Slot Engine (task-derived availability)"

    # ---- Time helpers (UTC naive internally) ----
    def _tz_name(self):
        return self.env.context.get("tz") or self.env.user.tz or "America/El_Salvador"

    def _to_utc_naive(self, dt_local_naive):
        if not dt_local_naive:
            return dt_local_naive
        tz = pytz.timezone(self._tz_name())
        local_dt = dt_local_naive if dt_local_naive.tzinfo else tz.localize(dt_local_naive)
        return local_dt.astimezone(pytz.UTC).replace(tzinfo=None)

    def _to_local_naive(self, dt_utc_naive):
        if not dt_utc_naive:
            return dt_utc_naive
        tz = pytz.timezone(self._tz_name())
        aware = pytz.UTC.localize(dt_utc_naive) if dt_utc_naive.tzinfo is None else dt_utc_naive.astimezone(pytz.UTC)
        return aware.astimezone(tz).replace(tzinfo=None)

    def _round_to_nearest_10(self, dt):
        if not dt:
            return dt
        remainder = dt.minute % 10
        minute = dt.minute - remainder + (10 if remainder >= 5 else 0)
        if minute == 60:
            return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return dt.replace(minute=minute, second=0, microsecond=0)

    # ---- Calendars / working windows ----
    def _get_calendar_for_team(self, team):
        return (
            team.calendar_id
            or getattr(team.lead_user_id, "resource_calendar_id", False)
            or self.env.company.resource_calendar_id
            or self.env.ref("resource.resource_calendar_std", raise_if_not=False)
        )

    def _iter_work_windows_local(self, team, day_date, time_start=None, time_end=None, lead_minutes=0):
        """
        Yield (shift_start_local, shift_end_local) for the team on a given day (local naive).
        """
        cal = self._get_calendar_for_team(team)
        if not cal:
            return
        weekday_str = str(day_date.weekday())
        attendances = cal.attendance_ids.filtered(lambda a: not a.display_type and a.dayofweek == weekday_str)
        for att in attendances:
            hour_from = att.hour_from
            hour_to = att.hour_to
            if time_start is not None:
                hour_from = max(hour_from, time_start)
            if time_end is not None:
                hour_to = min(hour_to, time_end)
            # convert float hours -> h/m
            h_from = int(hour_from)
            m_from = int(round((hour_from - h_from) * 60))
            h_to = int(hour_to)
            m_to = int(round((hour_to - h_to) * 60))
            shift_start = datetime.combine(day_date, time(h_from, m_from)) + timedelta(minutes=lead_minutes)
            shift_end = datetime.combine(day_date, time(h_to, m_to))
            if shift_end > shift_start:
                yield (shift_start, shift_end)

    # ---- Busy intervals from tasks (team-first with fallback to user overlap) ----
    def _get_team_users(self, team):
        users = self.env["res.users"]
        if team.lead_user_id:
            users |= team.lead_user_id
        # member_ids is typically fsm.team.member -> user_id
        member_users = team.member_ids.mapped("user_id").filtered(lambda u: u)
        users |= member_users
        return users

    def _task_fields(self):
        Task = self.env["project.task"]
        start_fields = [f for f in ("planned_date_begin", "date_start") if f in Task._fields]
        end_fields   = [f for f in ("planned_date_end", "date_end") if f in Task._fields]
        team_field = "team_id" if "team_id" in Task._fields else False
        return Task, start_fields, end_fields, team_field

    def _task_interval_utc(self, task, start_fields, end_fields, default_hours=1.0):
        start = next((getattr(task, f, False) for f in start_fields if getattr(task, f, False)), False)
        if not start:
            return (False, False)

        end = next((getattr(task, f, False) for f in end_fields if getattr(task, f, False)), False)

        if not end:
            hours = 0.0
            if "planned_hours" in task._fields and task.planned_hours:
                hours = float(task.planned_hours)
            if not hours:
                try:
                    hours = float(getattr(task.fsm_task_type_id, "default_planned_hours", 0.0) or 0.0)
                except Exception:
                    hours = 0.0
            hours = hours or default_hours or 1.0
            end = start + timedelta(hours=hours)

        return (start, end)

    def _busy_intervals_by_team_utc(
        self,
        teams,
        window_start_utc,
        window_end_utc,
        exclude_task_id=None,
        buffer_before_mins=0,
        buffer_after_mins=0,
    ):
        """
        Returns dict: team_id -> list[(busy_start_utc, busy_end_utc)] (UTC naive)
        Busy is derived from tasks in allowed stages, using either planned_* or date_* fields.
        """
        Task, start_fields, end_fields, team_field = self._task_fields()
        if not start_fields:
            return {t.id: [] for t in teams}

        travel_mins = int(
            self.env["ir.config_parameter"].sudo().get_param(
                "fsm_guided_intake.slot_travel_buffer_minutes", "0"
            ) or 0
        )
        buffer_before = timedelta(minutes=(buffer_before_mins or 0) + travel_mins)
        buffer_after = timedelta(minutes=(buffer_after_mins or 0) + travel_mins)

        # Build a “fallback user set” across all teams
        team_users_map = {t.id: self._get_team_users(t) for t in teams}
        all_users = self.env["res.users"].browse([])
        for u in team_users_map.values():
            all_users |= u

        allowed_stage_names = ["Scheduled", "To Be Scheduled", "In Progress"]

        # ---- Domain: start in any start_field < window_end_utc ----
        # OR-chain: (planned_date_begin < end) OR (date_start < end)
        start_dom = []
        for f in start_fields:
            start_dom = (["|"] + start_dom if start_dom else []) + [(f, "<", window_end_utc)]

        domain = [
            ("stage_id.name", "in", allowed_stage_names),
        ] + start_dom

        # ---- Domain: overlaps window start (end missing OR end > window_start_utc) ----
        # end_false_dom: (planned_end = False) OR (date_end = False)
        # end_gt_dom:    (planned_end > start) OR (date_end > start)
        if end_fields:
            end_false_dom = []
            for f in end_fields:
                end_false_dom = (["|"] + end_false_dom if end_false_dom else []) + [(f, "=", False)]

            end_gt_dom = []
            for f in end_fields:
                end_gt_dom = (["|"] + end_gt_dom if end_gt_dom else []) + [(f, ">", window_start_utc)]

            domain += ["|"] + end_false_dom + end_gt_dom

        if exclude_task_id:
            domain += [("id", "!=", exclude_task_id)]

        tasks = Task.search(domain)

        _logger.warning(
            "[SLOTDBG] start_fields=%s end_fields=%s team_field=%s window_utc=%s..%s teams=%s tasks_found=%s domain=%s",
            start_fields, end_fields, team_field,
            window_start_utc, window_end_utc,
            teams.ids,
            len(tasks),
            domain,
        )

        busy = {t.id: [] for t in teams}

        for task in tasks:
            start_utc, end_utc = self._task_interval_utc(task, start_fields, end_fields, default_hours=1.0)
            if not start_utc or not end_utc:
                continue

            b_start = start_utc - buffer_before
            b_end = end_utc + buffer_after

            assigned_team_ids = []

            if team_field and getattr(task, "team_id", False):
                assigned_team_ids = [task.team_id.id]
            elif all_users and "user_ids" in Task._fields and task.user_ids:
                for t in teams:
                    if task.user_ids & team_users_map.get(t.id, self.env["res.users"]):
                        assigned_team_ids.append(t.id)

            for tid in set(assigned_team_ids):
                if tid not in busy:
                    # Skip tasks assigned to teams outside the requested set
                    continue
                busy[tid].append((b_start, b_end))

        for tid in list(busy.keys()):
            busy[tid] = _merge_intervals(busy[tid])

        return busy


    # ---- Public API: compute slots ----
    def compute_top_slots(
        self,
        teams,
        start_dt_local,
        needed_hours,
        limit=3,
        date_end_local=None,
        time_start=None,
        time_end=None,
        exclude_task_id=None,
        buffer_before_mins=0,
        buffer_after_mins=0,
        lead_minutes=0,
    ):
        """
        Returns: list of {"start": local_naive_dt, "end": local_naive_dt, "team": fsm.team}
        """
        if not teams:
            return []
        start_dt_local = start_dt_local or fields.Datetime.now()
        start_dt_local = self._ensure_local_naive(start_dt_local)

        # Extend search horizon to 30 days so "more times" can keep paging forward
        search_end_local = date_end_local or (start_dt_local + timedelta(days=30))
        if date_end_local:
            search_end_local = self._ensure_local_naive(date_end_local)

        # Precompute busy intervals per team in UTC
        window_start_utc = self._to_utc_naive(start_dt_local)
        window_end_utc = self._to_utc_naive(search_end_local)

        busy_by_team = self._busy_intervals_by_team_utc(
            teams,
            window_start_utc,
            window_end_utc,
            exclude_task_id=exclude_task_id,
            buffer_before_mins=buffer_before_mins,
            buffer_after_mins=buffer_after_mins,
        )

        slots = []
        duration = timedelta(hours=max(needed_hours or 0.0, 1.0))

        logged = set()

        current_day = start_dt_local.date()
        while datetime.combine(current_day, time.min) < search_end_local:
            for team in teams:
                for shift_start_local, shift_end_local in self._iter_work_windows_local(
                    team, current_day, time_start=time_start, time_end=time_end, lead_minutes=lead_minutes
                ):
                    # enforce start boundary
                    shift_start_local_eff = max(shift_start_local, start_dt_local)
                    if shift_end_local <= shift_start_local_eff:
                        continue

                    # convert shift to UTC
                    shift_start_utc = self._to_utc_naive(shift_start_local_eff)
                    shift_end_utc = self._to_utc_naive(shift_end_local)

                    # subtract busy intervals in UTC
                    open_segments_utc = _subtract_intervals(
                        shift_start_utc, shift_end_utc, busy_by_team.get(team.id, [])
                    )
                    k = (team.id, current_day)
                    if k not in logged:
                        logged.add(k)
                        _logger.warning(
                            "TEAM=%s shift=%s..%s busy_sample=%s open=%s",
                            team.id,
                            self._to_local_naive(shift_start_utc), self._to_local_naive(shift_end_utc),
                            [(self._to_local_naive(a), self._to_local_naive(b)) for a, b in busy_by_team.get(team.id, [])[:5]],
                            [(self._to_local_naive(a), self._to_local_naive(b)) for a, b in open_segments_utc[:5]],
                        )


                    for open_start_utc, open_end_utc in open_segments_utc:
                        # pick earliest fitting start
                        cand_start_utc = open_start_utc
                        cand_start_local = self._to_local_naive(cand_start_utc)
                        cand_start_local = self._round_to_nearest_10(cand_start_local)
                        cand_start_utc = self._to_utc_naive(cand_start_local)

                        # ✅ NEW: don't allow rounding to move the start earlier than the segment start
                        cand_start_utc = max(cand_start_utc, open_start_utc)

                        cand_end_utc = cand_start_utc + duration
                        if cand_end_utc <= open_end_utc:
                            _logger.warning(
                                "SLOT team=%s open=%s..%s cand=%s..%s",
                                team.id,
                                self._to_local_naive(open_start_utc), self._to_local_naive(open_end_utc),
                                self._to_local_naive(cand_start_utc), self._to_local_naive(cand_end_utc),
                            )

                            slots.append({
                                "start": self._to_local_naive(cand_start_utc),
                                "end": self._to_local_naive(cand_end_utc),
                                "team": team,
                            })
                            break

            current_day += timedelta(days=1)

            slots.sort(key=lambda s: s["start"])
            if len(slots) >= limit:
                return slots[:limit]

        slots.sort(key=lambda s: s["start"])
        return slots[:limit]
    
    def _ensure_local_naive(self, dt_maybe_utc_naive):
        """
        Odoo gives UTC-naive datetimes (fields.Datetime.now(), datetime fields).
        Convert those to local-naive consistently before we do 'local' math.
        """
        if not dt_maybe_utc_naive:
            return dt_maybe_utc_naive
        # context_timestamp expects UTC-naive and returns tz-aware local
        local_aware = fields.Datetime.context_timestamp(self, dt_maybe_utc_naive)
        return local_aware.replace(tzinfo=None)
