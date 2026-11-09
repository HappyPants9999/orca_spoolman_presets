import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import orca_spoolman_presets as osp  # noqa: E402


def cand(name, rank, bundle_id=""):
    return {"name": name, "base": osp.base_name(name, bundle_id), "rank": rank}


CANDIDATES = [
    cand("Sunlu HS PLA+", osp.RANK_USER),
    cand("Sunlu HS PLA+ 2.0", osp.RANK_USER),
    cand("Sunlu PLA+ 2.0", osp.RANK_USER),
    cand("Sunlu Silk PLA+", osp.RANK_USER),
    cand("Sunlu HS PLA+ @Creality", osp.RANK_USER),
    cand("CCTREE PLA Matte", osp.RANK_USER),
    cand("Epelay PLA+", osp.RANK_USER),
    cand("VERTEXFIL PLA", osp.RANK_USER),
    cand("_local/spoolman/Sunlu PLA", osp.RANK_BUNDLE, "spoolman"),
    cand("SUNLU PLA+ 2.0 @System", osp.RANK_SYSTEM),
    cand("SUNLU PLA+ @System", osp.RANK_SYSTEM),
    cand("Generic PLA @System", osp.RANK_SYSTEM),
]

def best(vendor, material, name):
    found = osp.match_preset(osp.spool_keys(vendor, material, name), CANDIDATES)
    return found["name"] if found else None


class MatchTest(unittest.TestCase):
    def test_longest_line_wins_over_shorter(self):
        self.assertEqual(best("Sunlu", "PLA+", "HS PLA+ 2.0 Olive Green"), "Sunlu HS PLA+ 2.0")
        self.assertEqual(best("Sunlu", "PLA", "HS PLA+ White"), "Sunlu HS PLA+")

    def test_case_and_printer_suffix_are_ignored(self):
        self.assertEqual(best("SUNLU", "PLA", "Silk PLA+ Dual-Color Black Purple"), "Sunlu Silk PLA+")
        self.assertEqual(osp.base_name("Sunlu HS PLA+ @Creality"), "Sunlu HS PLA+")

    def test_user_preset_beats_system_on_a_tie(self):
        self.assertEqual(best("Sunlu", "PLA", "PLA+ 2.0 Black"), "Sunlu PLA+ 2.0")

    def test_color_only_names_match_through_the_material(self):
        self.assertEqual(best("Epelay", "PLA+", "Green"), "Epelay PLA+")
        self.assertEqual(best("Vertexfil", "PLA", "Cyan"), "VERTEXFIL PLA")
        self.assertEqual(best("CCTREE", "PLA", "Matte Navy Blue"), "CCTREE PLA Matte")
        self.assertEqual(best("Sunlu", "PLA", "Black"), "_local/spoolman/Sunlu PLA")

    def test_own_preset_without_vendor_matches_the_bare_name(self):
        candidates = CANDIDATES + [cand("Soleyin Ultra PLA", osp.RANK_USER),
                                   cand("Snapmaker PLA @System", osp.RANK_SYSTEM)]
        keys = osp.spool_keys("Creality", "PLA", "Soleyin Ultra PLA Ocean Blue")
        self.assertEqual(osp.match_preset(keys, candidates, "Soleyin Ultra PLA Ocean Blue")["name"],
                         "Soleyin Ultra PLA")
        # A built-in preset never matches the bare name: "PLA Silk" is not Snapmaker's.
        self.assertIsNone(osp.match_preset(["Miailuy PLA Silk"], [cand("PLA @System", osp.RANK_SYSTEM)], "PLA Silk"))

    def test_partial_words_do_not_match(self):
        self.assertIsNone(osp.match_preset(["Sunlu PLAX Red"], [cand("Sunlu PLA", 0)]))
        self.assertIsNone(best("Jayo", "PLA", "Olive Green"))

    def test_named_preset_by_base_name(self):
        found = osp.find_named_preset("sunlu hs pla+", CANDIDATES)
        self.assertEqual(found["name"], "Sunlu HS PLA+")
        self.assertIsNone(osp.find_named_preset("Deleted PLA", CANDIDATES))


PIP = [
    {"spool_id": 42, "name": "(#42) PLA Galaxy Blue - Overture - PipSpool", "base": "(#42) PLA Galaxy Blue - Overture - PipSpool",
     "rank": osp.RANK_USER, "usable": True},
    {"spool_id": 7, "name": "(#7) ASA Black - Polymaker - PipSpool", "base": "(#7) ASA Black - Polymaker - PipSpool",
     "rank": osp.RANK_USER, "usable": False},
]
SNAP = {"candidates": CANDIDATES + [{"name": PIP[0]["name"], "base": PIP[0]["base"], "rank": osp.RANK_USER}],
        "pipspool": PIP, "slots": ["Generic PLA @System"] * 4}


class PipSpoolTest(unittest.TestCase):
    def test_spool_id_from_names(self):
        self.assertEqual(osp.pipspool_spool_id("(#42) PLA Galaxy Blue - Overture - PipSpool"), 42)
        self.assertEqual(osp.pipspool_spool_id("PETG Black (#9) - Spoolman"), 9)           # older suffix
        self.assertEqual(osp.pipspool_spool_id("(#5) PLA Red - X - PipSpool @Sovol SV08"), 5)
        for name in ("(#5) PLA Red", "PLA - PipSpool", "(#1) (#2) - PipSpool", "(#0) x - PipSpool", "", None):
            self.assertIsNone(osp.pipspool_spool_id(name), name)

    def test_pipspool_beats_name_match(self):
        spool = {"filament": {"name": "HS PLA+ White", "material": "PLA", "vendor": {"name": "Sunlu"}}}
        cand, how, _ = osp.choose_preset(42, spool, SNAP)
        self.assertEqual((cand["name"], how), (PIP[0]["name"], "PipSpool"))

    def test_orca_preset_override_beats_pipspool(self):
        spool = {"extra": {"orca_preset": '"Sunlu HS PLA+"'}, "filament": {}}
        cand, how, _ = osp.choose_preset(42, spool, SNAP)
        self.assertEqual((cand["name"], how), ("Sunlu HS PLA+", "orca_preset"))

    def test_incompatible_pipspool_profile_is_reported_not_guessed(self):
        spool = {"filament": {"name": "HS PLA+ White", "material": "PLA", "vendor": {"name": "Sunlu"}}}
        cand, _, problem = osp.choose_preset(7, spool, SNAP)
        self.assertIsNone(cand)
        self.assertIn("not compatible", problem)

    def test_falls_back_to_name_match_then_explains(self):
        spool = {"filament": {"name": "HS PLA+ White", "material": "PLA", "vendor": {"name": "Sunlu"}}}
        cand, how, _ = osp.choose_preset(99, spool, SNAP)
        self.assertEqual(cand["name"], "Sunlu HS PLA+")
        self.assertIn("no PipSpool", how)
        cand, _, problem = osp.choose_preset(99, {}, SNAP)        # Spoolman unreachable
        self.assertIsNone(cand)
        self.assertIn("Synchronize now", problem)


class FakeMoonraker:
    def __init__(self, lanes, spools, spoolman_up=True):
        self.lanes, self.spools, self.up = lanes, spools, spoolman_up

    def lane_data(self):
        return self.lanes

    def spoolman(self, path):
        if not self.up:
            raise RuntimeError("Spoolman: unreachable")
        return self.spools.get(int(path.rsplit("/", 1)[-1]))


class SyncTest(unittest.TestCase):
    def run_sync(self, moonraker, snap=SNAP):
        selected, colored = [], []
        rows = osp.sync_slots(moonraker, snap, select=lambda s, n: selected.append((s, n)),
                              color=lambda s, c: colored.append((s, c)) or bool(c))
        return rows, selected, colored

    def test_spooltag_lanes_select_pipspool_profiles(self):
        lanes = {"T2": {"lane": "2", "spool_id": 42, "material": "PLA", "color": "#1E88E5"},
                 "T0": {"lane": "0", "material": ""}}
        rows, selected, colored = self.run_sync(FakeMoonraker(lanes, {42: {"filament": {"name": "Galaxy Blue"}}}))
        self.assertEqual(selected, [(3, PIP[0]["name"])])
        self.assertEqual(colored, [(3, "#1E88E5")])
        self.assertEqual(rows[0]["status"], "Empty lane")
        self.assertEqual(rows[1]["status"], "Selected (PipSpool), color set")

    def test_works_without_spoolman(self):
        lanes = {"T1": {"lane": "1", "spool_id": 42, "material": "PLA"}}
        rows, selected, _ = self.run_sync(FakeMoonraker(lanes, {}, spoolman_up=False))
        self.assertEqual(selected, [(2, PIP[0]["name"])])

    def test_already_set_and_too_few_slots(self):
        lanes = {"T0": {"lane": "0", "spool_id": 42}, "T6": {"lane": "6", "spool_id": 42}}
        snap = dict(SNAP, slots=[PIP[0]["name"]])
        rows, selected, _ = self.run_sync(FakeMoonraker(lanes, {}), snap)
        self.assertEqual(selected, [])
        self.assertTrue(rows[0]["status"].startswith("Already set (PipSpool)"))
        self.assertIn("only 1 filament slots", rows[1]["status"])


class MiscTest(unittest.TestCase):
    def test_decode_extra(self):
        self.assertEqual(osp.decode_extra('"Sunlu HS PLA+"'), "Sunlu HS PLA+")
        self.assertEqual(osp.decode_extra('""'), "")
        self.assertEqual(osp.decode_extra(None), "")

    def test_lanes(self):
        lanes = osp.lanes_from_lane_data({
            "T1": {"lane": "1", "material": ""},
            "T0": {"lane": "0", "material": "PLA", "spool_id": 77},
            "bad": "x",
        })
        self.assertEqual(lanes, [(1, 77, "PLA", ""), (2, None, "", "")])
        lanes = osp.lanes_from_lane_data({"T0": {"lane": "0", "spool_id": 25, "color": "#29cb94"}})
        self.assertEqual(lanes, [(1, 25, "", "#29CB94")])

    def test_normalize_color(self):
        for value, expected in (("#29cb94", "#29CB94"), ("29CB94FF", "#29CB94"), ("0x000000", "#000000"),
                                ("", ""), ("#12345", ""), (None, ""), ("red", "")):
            self.assertEqual(osp.normalize_color(value), expected, value)



if __name__ == "__main__":
    unittest.main()
