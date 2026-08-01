# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta

from odoo import api, fields, models, _


def _float_hour_to_time(hour_float):
    hour = int(hour_float)
    minute = int(round((hour_float - hour) * 60))
    return time(hour, minute)


class FsmDispatchPlanner(models.TransientModel):
    _name = "fsm.dispatch.planner"
    _description = "FSM Dispatch Planner"

    run_date = fields.Date(string="Run Date", default=lambda self: fields.Date.context_today(self) + timedelta(days=1))
    result_message = fields.Text(string="Results", readonly=True)

    @api.model
    def _skill_rank(self, skill):
        order = {"L1": 1, "L2": 2, "L3": 3}
        return order.get(skill or "", 0)

    @api.model
    def _pick_capacity_line(self, cap, needed_bucket, required_minutes):
        """Pick the best matching capacity line that can satisfy required minutes.

        Allows consuming higher-skill lines when exact bucket is unavailable.
        """
        buckets = cap.get("buckets", {}) if cap else {}
        needed_rank = self._skill_rank(needed_bucket or "L1")
        candidates = []
        for bucket_key, line in buckets.items():
            if self._skill_rank(bucket_key) < needed_rank:
                continue
            available = line.available_minutes or 0
            if available < required_minutes:
                continue
            # Prefer closest skill match, then highest remaining availability.
            distance = self._skill_rank(bucket_key) - needed_rank
            candidates.append((distance, -available, bucket_key, line))
        if not candidates:
            return (False, False)
        candidates.sort(key=lambda x: (x[0], x[1]))
        _, _, chosen_bucket, chosen_line = candidates[0]
        return (chosen_bucket, chosen_line)

    @api.model
    def _default_start_time(self, capacity_day):
        """Pick a start-of-day anchor for sequential booking placement."""
        if capacity_day and capacity_day.shift_id:
            return datetime.combine(
                capacity_day.date,
                _float_hour_to_time(capacity_day.shift_id.start_time or 8.0),
            )
        return datetime.combine(capacity_day.date if capacity_day else fields.Date.context_today(self), time(hour=8, minute=0))

    @api.model
    def _target_date(self, run_date=False):
        return fields.Date.to_date(run_date) if run_date else (fields.Date.context_today(self) + timedelta(days=1))

    @api.model
    def _reservations_for_date(self, target_date):
        return self.env["fsm.day.reservation"].search([
            ("dispatch_state", "=", "pending"),
            ("service_date", "=", target_date),
        ])

    @api.model
    def _load_capacity(self, target_date):
        days = self.env["fsm.capacity.day"].search([
            ("date", "=", target_date),
            ("state", "=", "ready"),
        ])
        cap_by_team = {}
        for day in days:
            bucket_map = {line.bucket_skill_level: line for line in day.line_ids}
            cap_by_team[day.team_id.id] = {
                "day": day,
                "buckets": bucket_map,
            }
        return cap_by_team

    @api.model
    def _score_candidates(self, reservation, candidates, cap_by_team, required_minutes):
        """Return best candidate team and used bucket given remaining minutes."""
        zone = reservation.zone or reservation.task_id.fsm_service_zone_name or ""
        scored = []
        needed_bucket = reservation.capacity_bucket or "L1"
        for team in candidates:
            cap = cap_by_team.get(team.id)
            if not cap:
                continue
            used_bucket, line = self._pick_capacity_line(cap, needed_bucket, required_minutes)
            if not line:
                continue
            remaining = line.available_minutes or 0
            score = (
                1 if zone else 0,  # placeholder for future zone weighting; same zone handled by grouping first
                remaining,
            )
            scored.append((score, team.id, used_bucket))
        if not scored:
            return (False, False)
        scored.sort(key=lambda x: (x[0][0], x[0][1]), reverse=True)
        return (scored[0][1], scored[0][2])

    @api.model
    def _candidate_teams(self, reservation):
        task_type = reservation.task_type_id
        preferred = task_type.preferred_team_ids if task_type else self.env["fsm.team"]
        capable = task_type.capable_team_ids if task_type else self.env["fsm.team"]
        # Keep order: preferred first, then capable minus preferred
        candidates = preferred | (capable - preferred)
        # Filter active and skill level meets bucket requirement
        needed = reservation.capacity_bucket or "L1"
        return candidates.filtered(lambda t: t.active and self._skill_rank(t.skill_level) >= self._skill_rank(needed))

    @api.model
    def _schedule_time(self, team_id, cap_by_team, cursor_map, required_minutes):
        cap = cap_by_team.get(team_id)
        if not cap:
            return (False, False)
        start = cursor_map.get(team_id)
        if not start:
            start = self._default_start_time(cap["day"])
        end = start + timedelta(minutes=required_minutes)
        cursor_map[team_id] = end
        return (start, end)

    def action_plan(self):
        target_date = self._target_date(self.run_date)
        reservations = self._reservations_for_date(target_date)
        if not reservations:
            self.result_message = _("No pending reservations for %s") % target_date
            return True

        cap_by_team = self._load_capacity(target_date)
        cursor_map = {}
        assigned = []
        skipped = []

        # Group by zone (same-zone processed together)
        zones = sorted(set(reservations.mapped(lambda r: r.zone or r.task_id.fsm_service_zone_name or "")))
        for zone in zones:
            zone_res = reservations.filtered(lambda r, z=zone: (r.zone or r.task_id.fsm_service_zone_name or "") == z)
            # Priority high to low, stable tie on id
            zone_res = zone_res.sorted(key=lambda r: (int(r.priority or 3) * -1, r.id))
            for res in zone_res:
                required_minutes = res.required_minutes or 0
                if required_minutes <= 0:
                    skipped.append((res, "Required minutes missing"))
                    continue
                bucket = res.capacity_bucket or (res.task_type_id.skill_level if res.task_type_id else "L1")
                res.capacity_bucket = bucket
                candidates = self._candidate_teams(res)
                if not candidates:
                    skipped.append((res, "No capable team"))
                    continue
                team_id, used_bucket = self._score_candidates(res, candidates, cap_by_team, required_minutes)
                if not team_id:
                    skipped.append((res, "No capacity available"))
                    continue
                start_dt, end_dt = self._schedule_time(team_id, cap_by_team, cursor_map, required_minutes)
                if not start_dt or not end_dt:
                    skipped.append((res, "Could not schedule time"))
                    continue

                # Update reservation and finalize (creates booking + delivery)
                res.write({
                    "capacity_bucket": used_bucket or res.capacity_bucket,
                    "assigned_team_id": team_id,
                    "assigned_start_datetime": start_dt,
                    "assigned_end_datetime": end_dt,
                })
                res.action_finalize_dispatch()

                # Update capacity counters
                day = cap_by_team[team_id]["day"]
                line = cap_by_team[team_id]["buckets"].get(used_bucket or bucket)
                if line:
                    line.available_minutes = max(0, (line.available_minutes or 0) - required_minutes)
                day.booked_minutes = (day.booked_minutes or 0) + required_minutes
                assigned.append(res)

        msg = _("Assigned %s reservations; %s skipped") % (len(assigned), len(skipped))
        if skipped:
            from collections import Counter
            reasons = Counter(reason for _, reason in skipped)
            reason_lines = "; ".join("%s × %s" % (count, reason) for reason, count in reasons.most_common())
            msg += "\nSkip reasons: " + reason_lines
        self.result_message = msg
        return {
            "type": "ir.actions.act_window",
            "res_model": "fsm.dispatch.planner",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    @api.model
    def cron_run(self):
        planner = self.create({"run_date": fields.Date.context_today(self) + timedelta(days=1)})
        planner.action_plan()
        return True
