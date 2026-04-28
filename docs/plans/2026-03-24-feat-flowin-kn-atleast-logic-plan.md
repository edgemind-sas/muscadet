---
title: "feat: Add at-least-k (k/n) logic to FlowIn"
type: feat
date: 2026-03-24
brainstorm: docs/brainstorms/2026-03-24-flowin-kn-logic-brainstorm.md
---

# feat: Add at-least-k (k/n) logic to FlowIn

## Overview

Add support for k/n voting logic on `FlowIn`: the flow input is `True` when at least `k` connected inputs are `True`. The user specifies `k` as an integer via the existing `logic` parameter: `add_flow_in(name="is_ok", logic=2)`.

This generalizes the existing logic: `"or"` is equivalent to `logic=1`, `"and"` is equivalent to `logic=n`.

PyCATSHOO provides native support via `IReference.sumValue(default)` which returns the count of `True` connections, and `nbCnx()` for the total count.

## Acceptance Criteria

- [ ] `add_flow_in(name="f", logic=2)` creates a flow that is `True` when >= 2 inputs are `True`
- [ ] `logic` field type changes to `Union[str, int]` with validation (int >= 1)
- [ ] k/n applied on both `var_in` and `var_fed_available` (consistent with and/or)
- [ ] No-connection behavior respects `var_in_default` via `sumValue(int(var_in_default))`
- [ ] `"and"` and `"or"` continue to work unchanged
- [ ] Display methods (`__repr__`, `__str__`, `get_logic_color`) handle int logic
- [ ] Tests cover: k=1 (equiv or), k=2 with 3 inputs, k > nbCnx (always False)
- [ ] `FlowOutOnTrigger.trigger_logic` also supports int (same pattern)

## Implementation - Files to Modify

### 1. `muscadet/flow.py` - `FlowIn` class

**6 methods to update:**

#### `logic` field (line 170)
```python
# Before
logic: str = pydantic.Field("or", description="Flow input logic and ; or ; k/n")

# After
logic: typing.Union[str, int] = pydantic.Field(
    "or", description="Flow input logic: 'and', 'or', or int k (at-least-k)"
)
```

#### `get_var_fed_available()` (lines 181-187)
```python
# Add branch
elif isinstance(self.logic, int):
    return self.var_fed_available.sumValue(int(self.var_available_in_default)) >= self.logic
```

#### `get_logic_color()` (lines 189-196)
```python
# Add branch
elif isinstance(self.logic, int):
    return f"{fg('yellow')}{self.logic}{attr('reset')}"
```

#### `__repr__()` (lines 197-216)
```python
# Add branch in try block
elif isinstance(self.logic, int):
    var_in_value = self.var_in.sumValue(int(self.var_in_default)) >= self.logic
```

#### `__str__()` (lines 218-241)
```python
# Same branch as __repr__
elif isinstance(self.logic, int):
    var_in_value = self.var_in.sumValue(int(self.var_in_default)) >= self.logic
```

#### `create_sensitive_set_flow_fed_in()` (lines 263-286) - CRITICAL
```python
elif isinstance(self.logic, int):
    k = self.logic

    def sensitive_set_flow_template():
        self.var_fed.setValue(
            self.var_in.sumValue(int(self.var_in_default)) >= k
            and self.var_fed_available.sumValue(int(self.var_available_in_default)) >= k
        )
```

### 2. `muscadet/flow.py` - `FlowOutOnTrigger` class

**3 locations to update:**

#### `trigger_logic` field (line 757)
```python
trigger_logic: typing.Union[str, int] = pydantic.Field(
    "or", description="Flow input logic: 'and', 'or', or int k (at-least-k)"
)
```

#### `add_automata()` - trigger_up condition (lines 804-817)
```python
elif isinstance(self.trigger_logic, int):
    k = self.trigger_logic

    def cond_method_12():
        return not (self.var_trigger_in.sumValue(0) >= k)
```

#### `add_automata()` - trigger_down condition (lines 823-836)
```python
elif isinstance(self.trigger_logic, int):
    k = self.trigger_logic

    def cond_method_21():
        return self.var_trigger_in.sumValue(0) >= k
```

### 3. `muscadet/cod3s_wrapper.py` - `InterfaceFlowIn`

#### `logic` field (line 14)
```python
logic: Union[str, int] = Field(
    "or", description="Flow input logic 'and' ; 'or' (default) ; int k (at-least-k)"
)
```

### 4. `tests/test_flow_in_kn_001.py` - NEW

```python
import muscadet
import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():

    class Source(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out(name="f1", var_prod_default=True)

    class TargetK2(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="f1", logic=2)

    system = muscadet.System(name="Sys")
    system.add_component(name="S1", cls="Source")
    system.add_component(name="S2", cls="Source")
    system.add_component(name="S3", cls="Source")
    system.add_component(name="T", cls="TargetK2")

    system.auto_connect("S.*", "T")

    return system


def test_all_sources_ok(the_system):
    """3 sources connected, k=2: all True -> fed=True"""
    the_system.isimu_start()
    assert the_system.comp["T"].flows_in["f1"].var_fed.value() is True


def test_one_source_fails(the_system):
    """Remove one source: 2 remaining >= k=2 -> still True"""
    # Test with failure mode that disables one source
    ...


def test_two_sources_fail(the_system):
    """Only 1 source left: 1 < k=2 -> fed=False"""
    ...


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
```

## Error Handling Updates

Update all `raise ValueError("FlowIn logic must be 'and' or 'or'")` messages to:
```python
raise ValueError("FlowIn logic must be 'and', 'or', or a positive integer")
```

## References

- Brainstorm: `docs/brainstorms/2026-03-24-flowin-kn-logic-brainstorm.md`
- PyCATSHOO API: `IReference.sumValue(default)`, `IReference.nbCnx()`
- Existing logic implementation: `muscadet/flow.py:263-286`
