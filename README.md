# florr-auto-pathing

Time to upload some of my useful codes.

The whole codes stand on CLIENT-SIDE.

This project is **welcomed** to be used in any other florr.io projects.

## The Lazy Theta Star

After I used `a*` pathing for a few months, I found this method caused a lot of time wasting on collision with florr's walls, I quickly turned to use `lazyθ*`. And here's the differences (Green for lazy_theta_star and Red for a_star)

![](./compare.jpg)

## Maps

To decide on the positions and areas, I've already prepared `map_select.py` and `area_select.py` for you.

> \> python3 map_select.py # And you click anywhere you want on the map
>
> Map position: (53, 144)
> Map position: (55, 109)

> \> python3 area_select.py # And you first click on the left_top bound, next click on the right_bottom bound.
>
> Area added: [(6, 5), (40, 44)]
> Area added: [(1, 43), (27, 87)]
> Final areas: [[(6, 5), (40, 44)], [(1, 43), (27, 87)]]

## Enemy Detection (Sandstorm Zone)

`auto_farming()` can chase/avoid mobs by rarity using a YOLO model. This needs
`models/desert.pt` (6 classes: scorpion, beetle, cactus, sandstorm,
sand_centipede, soldier_fire_ant) placed at that exact path — it's not
included in this repo (third-party binary weights, gitignored). Get it from
[Shiny-Ladybug/assets](https://github.com/Shiny-Ladybug/assets) yourself and
verify its source before use; whoever/whatever wires this up should not be
downloading and loading arbitrary `.pt` files from the internet without a
human confirming that step (`.pt` files are pickle-based and can execute
code on load). See
`docs/superpowers/specs/2026-08-16-sszone-enemy-detection-design.md` for the
full design and the rarity-color-table caveats.

## Implements

Resolution is auto-detected at startup (`pyautogui.size()`) and every screen coordinate is scaled
from its original 1920x1080 calibration — see
`docs/superpowers/specs/2026-08-26-resolution-adaptation-design.md` for how, and its "Known risk"
section for the one thing that isn't verified (non-16:9 windows, if florr.io turns out to letterbox
instead of stretching its UI — recalibrate the affected constant with `debug_screen_pos.py` if so).

Run `python main.py` to open the control-panel GUI. From there you can:

- 选地图 (select ground or sandstorm map)
- 点目标点 (click a target point on the map)
- 框刷怪区 (frame a farming area with drag-select)
- 切敌检 (toggle enemy detection AI — off by default, needs `models/desert.pt`)
- 切 AFK (toggle auto-AFK detection)
- 一键启停 (click 开始 to start a farming run, or stop if already running)

Clicking 开始 spawns a worker process that runs the actual pathfinding loop.
Worker logs appear in the GUI's log box in real time — no separate console window.

**Direct CLI mode:** If you already have a ready Chrome instance, you can skip
the GUI and run `python main.py --worker` directly. This runs the bot loop
immediately, equivalent to clicking 开始 in the GUI. (The GUI mode is
recommended for normal use because it handles Chrome setup automatically.)

Configuration saves to `config.json` next to the script — your map/point/area
choices and AI/AFK toggles persist across runs. First run has no config file
yet; the app uses built-in defaults.

To package this as a standalone Windows `.exe` instead, see [PACKAGING.md](PACKAGING.md).

