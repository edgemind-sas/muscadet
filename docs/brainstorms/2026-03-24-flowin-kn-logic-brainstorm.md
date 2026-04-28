# Brainstorm: FlowIn k/n Logic

**Date:** 2026-03-24
**Status:** Ready for planning

## What We're Building

Add k/n (at-least-k) logic to `FlowIn`, allowing a flow input to be considered `True` when at least `k` connected inputs are `True`, regardless of the total number of connections `n`.

Currently `FlowIn.logic` only supports `"and"` (all inputs True) and `"or"` (at least one input True). The k/n logic generalizes this: `"and"` is equivalent to k=n, `"or"` is equivalent to k=1.

## Why This Approach

PyCATSHOO's `IReference` already provides the building blocks:
- `sumValue(default)` on a boolean reference returns the count of connected values that are `True` (or `default` if no connection)
- `nbCnx()` returns the total number of connections

This makes the implementation straightforward: `sumValue(default) >= k`.

## Key Decisions

1. **API: `logic` accepts an integer directly**
   - `add_flow_in(name="is_ok", logic=2)` means "at least 2 inputs must be True"
   - `logic` field type changes from `str` to `Union[str, int]`
   - `"and"` and `"or"` remain valid as strings

2. **No-connection behavior: respect `var_in_default`**
   - When no connection exists, `sumValue(int(var_in_default))` is used
   - If `var_in_default=False` (default): `sumValue(0)` returns 0, so `0 >= k` is `False`
   - Consistent with how `andValue(var_in_default)` / `orValue(var_in_default)` work for and/or

3. **Applied on both var_in and var_fed_available**
   - Same k threshold on both the value and the availability references
   - Consistent with the and/or pattern where both use the same logic

## Impact on Code

### `muscadet/flow.py` - `FlowIn` class
- `logic` field: type `Union[str, int]`, validator to ensure int >= 1
- `get_var_fed_available()`: add `elif isinstance(self.logic, int)` branch using `sumValue() >= k`
- `create_sensitive_set_flow_fed_in()`: add branch for int logic
- `__repr__` / `__str__`: handle int display
- `get_logic_color()`: handle int case

### `muscadet/cod3s_wrapper.py` - `InterfaceFlowIn`
- Update `logic` field type to `Union[str, int]`

### Tests
- New test file `tests/test_flow_in_kn_001.py`
- Test cases: k=1 (equivalent to or), k=n (equivalent to and), k between 1 and n, edge case k > n

## Open Questions

None - design is clear.

## Sensitive Method Template (Reference)

```python
# For logic = k (int):
def sensitive_set_flow_template():
    self.var_fed.setValue(
        self.var_in.sumValue(int(self.var_in_default)) >= k
        and self.var_fed_available.sumValue(int(self.var_available_in_default)) >= k
    )
```
