# FSM Guided Intake Wizard - Changes Summary

## Overview
This document summarizes all modifications made to the FSM Task Intake Wizard based on the requested changes.

---

## Changes Made

### 1. Screen 1 (Task Type Selection) - Planned Hours Field REMOVED
**File**: `fsm_task_intake_wizard.py` and `fsm_task_intake_wizard_views.xml`

**Changes**:
- **Python Model** (lines 93-95):
  - Changed `planned_hours` from a simple field with default to a computed field
  - Added `@api.depends("task_type_id")` decorator
  - Created `_compute_planned_hours()` method that automatically pulls the value from `task_type_id.default_planned_hours`
  
- **XML View** (line 21):
  - Made the `planned_hours` field invisible on screen 1
  - The field still exists in the model but is no longer user-editable

**Result**: The planned hours are now automatically determined from the task type record, not manually entered by the user.

---

### 2. Screen 2 (Customer Selection) - "Warnings" Title REMOVED
**File**: `fsm_task_intake_wizard_views.xml`

**Changes**:
- **XML View** (line 29):
  - Removed the `<label for="service_address_id" string="Warnings" colspan="2"/>` line
  - Warning messages still display but without the "Warnings" label above them

**Result**: Warning messages appear directly without a section title.

---

### 3. Screen 3 (Products Selection) - Multiple Changes

#### 3a. Updated Banner Message
**File**: `fsm_task_intake_wizard_views.xml`

**Changes**:
- **XML View** (lines 40-42):
  - Replaced the conditional warning message with a permanent info banner
  - New message: "Select an Existing Sales Order or Add Products to the Task"
  - Changed from `alert-info` with condition to always-visible `alert-info`

#### 3b. Added Validation for Missing Products/SO
**File**: `fsm_task_intake_wizard.py`

**Changes**:
- **Python Model** (line 137):
  - Added new computed field `warning_no_products_or_so`
  
- **Compute Method** (lines 221-227):
  - Added logic in `_compute_warnings()` to check if task type requires products but neither SO nor products are provided
  
- **Preflight Errors** (lines 237-239):
  - Added validation in `_preflight_errors()` to prevent task creation without products when required

- **XML View** (lines 53-56):
  - Added warning div that displays when products are required but missing

**Result**: Users see a warning if the task type requires products but they haven't selected a Sales Order or added products.

#### 3c. Removed Message Box Left of Product Table
**File**: `fsm_task_intake_wizard_views.xml`

**Changes**:
- **XML View** (lines 38-56):
  - Removed the conditional alert-info div that was displaying "This task type requires at least one product/service line"
  - The product table now displays without any message box to its left

**Result**: The product table displays cleanly without a message box beside it.

---

### 4. Screen 4 (Schedule Selection) - Fixed Team Display and Time Slot Formatting

#### 4a. Fixed "Qualified Teams" Title and Display
**File**: `fsm_task_intake_wizard_views.xml`

**Changes**:
- **XML View** (lines 59-62):
  - Wrapped the `qualified_team_ids` field in a `<group string="Qualified Teams">` element
  - This ensures both the title and the team buttons appear correctly

**Result**: The "Qualified Teams" title now appears above the team tags/buttons.

#### 4b. Fixed Time Slot Formatting
**File**: `fsm_task_intake_wizard.py`

**Changes**:
- **Slot Selection Method** (lines 154-169):
  - Rewrote `_get_slot_selection()` to use computed slot labels directly
  - Removed reliance on context-based labels
  
- **Slot Label Formatting** (lines 380-403):
  - Updated `_compute_slots()` method to format labels properly
  - Changed format from `"%a %Y-%m-%d %H:%M"` to `"%a, %B %d"` for date
  - Format now displays as: **"Tue, December 31, 10:00 - 12:00"**
  - Used proper strftime formatting: `%a` (day), `%B` (month name), `%d` (day number)

**Result**: Time slots now display with human-readable dates like "Tue, December 31, 10:00 - 12:00" instead of "Option 1, Option 2, Option 3".

---

## Files Modified

1. **`wizard/fsm_task_intake_wizard.py`**
   - Changed `planned_hours` to computed field
   - Added `warning_no_products_or_so` field
   - Updated `_compute_warnings()` method
   - Updated `_preflight_errors()` method
   - Rewrote `_get_slot_selection()` method
   - Updated slot label formatting in `_compute_slots()` method

2. **`wizard/fsm_task_intake_wizard_views.xml`**
   - Made `planned_hours` invisible on screen 1
   - Removed "Warnings" label on screen 2
   - Updated banner message on screen 3
   - Removed message box on screen 3
   - Added new warning div for missing products/SO on screen 3
   - Fixed "Qualified Teams" grouping on screen 4

---

## Installation Instructions

1. **Backup your current files** before making changes
2. **Replace** the existing files with the modified versions:
   - `/path/to/odoo/addons/fsm_guided_intake/wizard/fsm_task_intake_wizard.py`
   - `/path/to/odoo/addons/fsm_guided_intake/wizard/fsm_task_intake_wizard_views.xml`
3. **Restart Odoo** service
4. **Update the module** in Odoo:
   - Go to Apps
   - Remove "Apps" filter
   - Search for "FSM Guided Intake"
   - Click "Upgrade"

---

## Testing Checklist

After installation, test the following:

- [ ] Screen 1: Planned hours field is not visible but value is set from task type
- [ ] Screen 2: Warning messages appear without "Warnings" title
- [ ] Screen 3: Banner message reads "Select an Existing Sales Order or Add Products to the Task"
- [ ] Screen 3: Warning appears if products required but not provided
- [ ] Screen 3: No message box appears to the left of the product table
- [ ] Screen 4: "Qualified Teams" title appears above team buttons
- [ ] Screen 4: Time slots show formatted dates like "Tue, December 31, 10:00 - 12:00"
- [ ] Task creation: Planned hours are correctly set from task type
- [ ] Task creation: Validation prevents creation when products are required but missing

---

## Notes

- All changes are backward compatible with existing data
- The `planned_hours` field still exists and is stored - it's just no longer user-editable
- Time slot formatting uses Python's strftime which may vary slightly by locale
- The qualified teams display now properly shows both the title and the team buttons
