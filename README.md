# PipSpool Slot Sync

An OrcaSlicer plugin that puts the right [PipSpool](https://github.com/Gadonk/pipspool-orcaslicer) profile into each filament slot, based on the spool actually loaded in each tool of a Moonraker printer.

It is a fork of [DIEHARDave/orca_spoolman_presets](https://github.com/DIEHARDave/orca_spoolman_presets), changed for a setup where PipSpool owns the filament profiles:

- **It matches PipSpool profiles by spool ID.** The lane holds spool 42, so the slot gets `(#42) … - PipSpool`. This uses the same rule PipSpool uses to recognize its own profiles: `(#<id>)` appears exactly once and the name ends in ` - PipSpool`. Your naming template doesn't matter.
- **It never creates presets.** The original's "Create missing presets" feature is removed, and so is the code that writes preset bundles.
- **It still works when Spoolman is unreachable.** PipSpool matching needs only the `spool_id` in `lane_data`.

## How a slot's preset is chosen

1. The `orca_preset` extra field on the spool, then on its filament. This is an explicit override.
2. **The PipSpool profile for the spool.** If that profile exists but is hidden or not compatible with the selected printer, the plugin reports it instead of guessing.
3. Otherwise, the original's name match (`<vendor> <filament name>` against your presets), labeled "no PipSpool profile" so you can tell.

Selecting a preset resets the slot's color, so the plugin then sets it back from the lane's color where Orca allows it.

## Requirements

- An OrcaSlicer build with Python plugins (2.5 alpha / nightly or later).
- Moonraker `lane_data` with a `spool_id` per lane, written by [spooltag](https://github.com/HappyPants9999/spooltag) (NFC scans), Happy Hare, AFC or similar.
- Moonraker with a `[spoolman]` section. Spoolman is reached only through `/server/spoolman/proxy`.
- PipSpool synchronized, and OrcaSlicer restarted after PipSpool adds profiles.

## Install

1. If the original Orca Spoolman Presets plugin is installed, uninstall it first.
2. In OrcaSlicer, open **Plugins → Install local plugin** and pick `orca_spoolman_presets.py`.
3. Run **PipSpool Slot Sync**, enter the Moonraker URL (for example `http://192.168.1.50:7125`), and click **Save**.

## Use

1. Press Orca's own **Synchronize filament list**. This sets the slot count and colors.
2. Press **Sync slots from printer** in this plugin.

The selections are ordinary slot choices in the project, exactly as if you had picked them by hand.

## Development

```
python -m unittest discover -s tests
```

## License

GPL-3.0, as the original. See [LICENSE](LICENSE).
