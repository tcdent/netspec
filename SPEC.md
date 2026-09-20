# netspec — a small board-level HDL in Python

Status: draft 0.8. Scope: describe a PCB's parts and connections as code, and have the description check itself.
No dependencies beyond the Python standard library.

## Principles

1. **Facts live on parts, topology lives in the design.** A part definition records what each pin *is* (from the
   datasheet, cited). A design only says what connects to what. Rules compare the two.
2. **Emergent testing.** Designers never write test cases. Rules ship with the library and with part definitions;
   the mutation engine breaks every design automatically and proves the rules notice.
3. **Nothing is an arbitrary label.** Net names are for humans. Every check is driven by pin kinds, disciplines,
   units, ratings and values.
4. **Units are types.** `V(3.3)`, `mA(8)`, `k(56.2)`, `uF(47)`. Mixing dimensions or using a bare number raises.
5. **Light stack.** Plain Python, runs anywhere, exports to other tools instead of depending on them.

## Concepts (and where they come from)

| netspec | Meaning | Heritage |
|---|---|---|
| `Part`, `Pin`, `K` kinds | A component and the electrical role of each pin: `PWR_IN/OUT`, `GND`, `TX/RX`, `CLK_IN/OUT`, `IN/OUT/OD/BIDIR`, `ANALOG`, `PASSIVE`, `NC`, `DNC` | Verilog module + port direction |
| `Net` | A set of connected pins. Voltage and discipline are *inferred*, never declared | Verilog wire |
| `Bundle`, `BundleType` | Named group of signals with two roles (`host`/`device`). `c.link(a, b)` connects a whole interface and requires opposite roles | SystemVerilog interface + modport |
| Discipline | The electrical language of a pin: `rail`, `gnd`, `pcie`, `hcsl`, `logic 3.3 V`, `analog`. All non-passive pins on a net must agree | Verilog-AMS discipline |
| Domain crossing | A plain two-pin passive may not bridge two different logic voltages unless it is DNP or a declared level shifter | UPF / IEEE 1801 |
| Part rules | Checks attached to a part definition: `feedback_divider`, `config_resistor`, `requires_cap` | SystemVerilog assertions bound to a module |
| Power facts | `Load`, `Converter`, fuse `rating`, pin `imax`. The power tree is *derived* from the netlist | psucalc (tcdent) |
| Mutation test | Auto-generated broken variants of the design | mutation testing |

## Built-in rules (run by `check`)

- Hygiene: every pin is connected, or is `NC`, or is explicitly `nc()`; `DNC` pins stay open; a net with a single pin
  is an error (a net that ends at a DNP option is deliberate and passes).
- Discipline agreement per net; `GND` holds only ground and passive pins.
- Power: each power input has a source; one source per net unless all are `bus` feeds at equal voltage; sources
  propagate through `power_pass` parts; source voltage inside every input's `vmin..vmax`; every powered net has a
  capacitor to ground; capacitor ratings derated to 80 %.
- Differential: TX meets exactly one RX (CLK_OUT meets CLK_IN); polarity and lane match; both halves of a pair land
  on the same part; one link lands on one partner part.
- Logic: inputs have a driver or bias; at most one push-pull driver.
- Domain crossing (above). Part rules. Design rules (`c.rules`).

`derive` (after `check`) walks from each externally fed net through fuses and converters to loads, computing peak and
average current, converter loss and junction temperature, and checking every rating on the way. A power input whose
part declares no load is an error: nothing may draw current unbudgeted.

## Mutation operators

| Operator | Mutation | Policy |
|---|---|---|
| `drop_pin` | disconnect one pin | must catch |
| `short_to_rail` | move a non-passive pin onto the highest rail | must catch |
| `connect_dnc` | ground a do-not-connect pin | must catch |
| `swap_polarity`, `swap_tx_rx`, `swap_lanes` | the classic high-speed wiring mistakes | must catch |
| `underrate` | capacitor rated below its net; fuse rating quartered | must catch |
| `scale_value` | resistor/capacitor value x10 and x0.1 | coverage |
| `populate_dnp` | fit a DNP part | coverage |

A surviving *must catch* mutant is a **rule gap**: a library bug, exit status 1. Surviving *coverage* mutants are
listed as "values no rule constrains" so the designer can see what is unchecked (LED resistors: fine; a feedback
resistor: would be alarming).

## Known limits (honest list)

- A rule can only be as right as the part definition. If a pin's role is typed wrong in the library, wiring that
  matches the mistake passes. Mitigation: `source=` citation and `verified=` flag per part; unverified parts are
  listed on every run. Bundles add one internal cross-check (pin kind vs. role expectation).
- Geometry is checked, not solved: `physical.py` states widths, pair rules, skew and via limits and `layout.py` holds a
  routed board to them, but impedance comes from the fab's stackup, and return paths stay with review.
- Efficiency and thermal numbers are single-point estimates, not curves.
- Small loads (LEDs, pull-ups) are not yet derived from R and Vf.
- The spec checks intent and local rules; it does not simulate. Current distribution in pours, plane splits,
  voltage drop across zones and return-path continuity stay with human review.
- The writers and the layout reader are exercised against KiCad 10.99 (the 11 development line) on one real board:
  KiCad loads, upgrades, DRCs and ERCs what is written, schematic and board agree net for net, and the reader gets
  pads, tracks, vias and zones back from the saved file. The netlist import path has not been tried in KiCad.

## Layout of the workspace

The library is its own repository; each board is a project of its own that depends on it - from git, or by path
while both are being worked on side by side.

    netspec/                 the library (this repository)
        src/netspec/         units.py core.py power.py mutate.py physical.py layout.py kicad.py
        README.md SPEC.md pyproject.toml
    <project>/               one repository per board, e.g. m2x2_slimsas/
        parts.py             part definitions, each citing its source
        board.py             a design: parts + connections only
        placement.py         positions, outline, legends, planes      (when netspec writes the board)
        routing*.py          tracks, vias, pours
        pyproject.toml       netspec = { git = "https://github.com/tcdent/netspec" }

Run a design from its project directory with `uv run python board.py`.

## Roadmap

1. Exporters as backends: KiCad netlist (stable IDs so re-import preserves layout), label-style `.kicad_sch`,
   structural Verilog for an independent Yosys lint, JSON.
2. `qty=` on system-level stacks; derive LED and pull-up currents.
3. Tolerances as intervals (`V(3.3, pct=5)`), so range checks use worst case.
4. More bundle types: SMBus, SATA, USB; more disciplines with explicit compatibility tables.
5. Split `parts.py` into a versioned library once a second board exists.
