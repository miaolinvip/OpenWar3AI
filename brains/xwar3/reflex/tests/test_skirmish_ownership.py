import pathlib,sys,unittest
from types import SimpleNamespace as N
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import mod_combat as C


class Ownership(unittest.TestCase):
    def test_only_new_brain_battle_leases_disable_legacy_group_retreat(self):
        army=[N(addr=i) for i in range(6)]
        claims=N(lease_of=lambda a:(1,76,1000) if a<4 else None)
        self.assertTrue(C.brain_controls_battle(claims,army))
        self.assertFalse(C.brain_controls_battle(N(lease_of=lambda a:(1,50,1000)),army))
        self.assertFalse(C.brain_controls_battle(N(lease_of=lambda a:(1,75,1000)),army))
        self.assertFalse(C.brain_controls_battle(N(lease_of=lambda a:(3,76,1000)),army))
        self.assertFalse(C.brain_controls_battle(N(lease_of=lambda a:None),army))
        self.assertFalse(C.brain_controls_battle(N(),army))
