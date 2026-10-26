"""Hours claims: housekeepers claim, owners approve and mark paid. Run with:  python3 -m unittest discover -s tests -v"""
from __future__ import annotations

from datetime import date, timedelta

from test_api import H, run_cli
from test_jobs import KindTest

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


class HoursTest(KindTest):
    def setUp(self):
        super().setUp()
        run_cli(["create-user", "--username", "dana", "--name", "Dana", "--role", "staff", "--password", "dana-pass-1"])

    def claim(self, **body) -> dict:
        r = self.c.post("/api/hours", json={"date": YESTERDAY, "minutes": 180, **body}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["claim"]

    def test_a_housekeeper_claims_and_sees_their_own(self):
        maria = self.login("maria")
        c = self.claim(note="  Ironing as well  ")
        self.assertEqual(set(c), {"id", "date", "minutes", "note", "status", "createdAt", "by", "approvedAt",
                                  "approvedBy", "paidAt", "paidBy"})
        self.assertEqual((c["date"], c["minutes"], c["note"], c["status"]), (YESTERDAY, 180, "Ironing as well", "submitted"))
        self.assertEqual(c["by"], {"id": maria["id"], "displayName": "Maria"})
        self.assertEqual((c["approvedAt"], c["approvedBy"], c["paidAt"], c["paidBy"]), (None, None, None, None))
        today = self.claim(date=TODAY, minutes=15)
        also_today = self.claim(date=TODAY, minutes=960)  # several claims on one day are fine
        self.assertEqual([x["id"] for x in self.c.get("/api/hours").json()["claims"]],
                         [also_today["id"], today["id"], c["id"]])  # newest day first
        self.assertEqual(self.c.get("/api/state").json()["hours"], self.c.get("/api/hours").json()["claims"])
        self.login("dana")
        self.claim()
        self.assertEqual(len(self.c.get("/api/hours").json()["claims"]), 1)  # only her own
        self.login("owen")
        self.assertEqual(len(self.c.get("/api/hours").json()["claims"]), 4)  # everyone's
        self.assertEqual(len(self.c.get("/api/state").json()["hours"]), 4)

    def test_validation(self):
        self.login("maria")
        future = (date.today() + timedelta(days=1)).isoformat()
        quarter = "Hours go in quarter hours, from 0.25 to 16."
        for body, message in (({"date": future, "minutes": 60}, "You can't claim hours for a future day."),
                              ({"date": YESTERDAY, "minutes": 10}, quarter),
                              ({"date": YESTERDAY, "minutes": 1000}, quarter),
                              ({"date": YESTERDAY, "minutes": 50}, quarter),
                              ({"date": YESTERDAY, "minutes": 0}, quarter),
                              ({"date": YESTERDAY, "minutes": True}, quarter),
                              ({"date": YESTERDAY, "minutes": "60"}, quarter),
                              ({"date": YESTERDAY, "minutes": 60.5}, quarter),
                              ({"date": YESTERDAY}, quarter),
                              ({"date": "29/09/2026", "minutes": 60}, "Dates must look like 2026-09-29."),
                              ({"minutes": 60}, "Dates must look like 2026-09-29."),
                              ({"date": "2026-02-30", "minutes": 60}, "That date doesn't exist."),
                              ({"date": YESTERDAY, "minutes": 60, "note": "x" * 501},
                               "'note' is too long (max 500 characters).")):
            with self.subTest(body=body):
                self.refused(self.c.post("/api/hours", json=body, headers=H), 400, message)
        self.assertEqual(self.claim(minutes=60.0)["minutes"], 60)  # a whole number sent as a float is fine
        self.assertEqual(self.claim(date="2026-09-30")["date"], "2026-09-30")  # any past day, not just visit days

    def test_owners_and_builders_cannot_claim(self):
        self.login("owen")
        self.refused(self.c.post("/api/hours", json={"date": YESTERDAY, "minutes": 60}, headers=H), 403,
                     "Only staff claim hours.")
        self.login("ondrej")
        self.refused(self.c.post("/api/hours", json={"date": YESTERDAY, "minutes": 60}, headers=H))
        self.refused(self.c.get("/api/hours"))
        self.refused(self.c.patch("/api/hours/1", json={"minutes": 60}, headers=H))
        self.refused(self.c.delete("/api/hours/1", headers=H))

    def test_claimant_edits_and_deletes_while_submitted(self):
        self.login("maria")
        c = self.claim()
        edited = self.c.patch(f"/api/hours/{c['id']}", json={"minutes": 195, "note": "Plus the oven",
                                                             "date": TODAY}, headers=H).json()["claim"]
        self.assertEqual((edited["minutes"], edited["note"], edited["date"]), (195, "Plus the oven", TODAY))
        self.refused(self.c.patch(f"/api/hours/{c['id']}", json={"minutes": 7}, headers=H), 400,
                     "Hours go in quarter hours, from 0.25 to 16.")
        self.refused(self.c.patch(f"/api/hours/{c['id']}", json={}, headers=H), 400, "Nothing to change.")
        self.refused(self.c.patch(f"/api/hours/{c['id']}", json={"status": "approved"}, headers=H), 403,
                     "Only an owner can do that.")
        self.assertEqual(self.c.delete(f"/api/hours/{c['id']}", headers=H).json(), {"ok": True})
        self.assertEqual(self.c.get("/api/hours").json()["claims"], [])
        self.refused(self.c.delete(f"/api/hours/{c['id']}", headers=H), 404, "That claim doesn't exist.")

    def test_claimant_cannot_change_an_approved_claim(self):
        self.login("maria")
        c = self.claim()
        self.login("owen")
        self.c.patch(f"/api/hours/{c['id']}", json={"status": "approved"}, headers=H)
        self.login("maria")
        for status in ("approved", "paid"):
            with self.subTest(status=status):
                if status == "paid":
                    self.login("owen")
                    self.c.patch(f"/api/hours/{c['id']}", json={"status": "paid"}, headers=H)
                    self.login("maria")
                self.refused(self.c.patch(f"/api/hours/{c['id']}", json={"minutes": 60}, headers=H), 409,
                             "That claim has already been approved.")
                self.refused(self.c.delete(f"/api/hours/{c['id']}", headers=H), 409,
                             "That claim has already been approved.")
        self.assertEqual(self.c.get("/api/hours").json()["claims"][0]["minutes"], 180)

    def test_another_housekeepers_claim_is_a_404(self):
        self.login("maria")
        c = self.claim()
        self.login("dana")
        for r in (self.c.patch(f"/api/hours/{c['id']}", json={"minutes": 60}, headers=H),
                  self.c.delete(f"/api/hours/{c['id']}", headers=H),
                  self.c.patch("/api/hours/nope", json={"minutes": 60}, headers=H)):
            self.refused(r, 404, "That claim doesn't exist.")

    def test_owner_approves_pays_and_undoes(self):
        self.login("maria")
        c = self.claim()
        owen = self.login("owen")
        stamp = {"id": owen["id"], "displayName": "Owen"}
        approved = self.c.patch(f"/api/hours/{c['id']}", json={"status": "approved"}, headers=H).json()["claim"]
        self.assertEqual((approved["status"], approved["approvedBy"], approved["paidAt"]), ("approved", stamp, None))
        self.assertIsNotNone(approved["approvedAt"])
        paid = self.c.patch(f"/api/hours/{c['id']}", json={"status": "paid"}, headers=H).json()["claim"]
        self.assertEqual((paid["status"], paid["paidBy"]), ("paid", stamp))
        self.assertEqual(paid["approvedAt"], approved["approvedAt"])  # approval keeps its own time
        back = self.c.patch(f"/api/hours/{c['id']}", json={"status": "approved"}, headers=H).json()["claim"]
        self.assertEqual((back["paidAt"], back["paidBy"]), (None, None))
        self.assertEqual(back["approvedAt"], approved["approvedAt"])
        undone = self.c.patch(f"/api/hours/{c['id']}", json={"status": "submitted"}, headers=H).json()["claim"]
        self.assertEqual((undone["approvedAt"], undone["approvedBy"], undone["paidAt"]), (None, None, None))
        # paying a claim nobody approved approves it as well
        straight = self.c.patch(f"/api/hours/{c['id']}", json={"status": "paid"}, headers=H).json()["claim"]
        self.assertEqual((straight["approvedBy"], straight["paidBy"]), (stamp, stamp))
        self.refused(self.c.patch(f"/api/hours/{c['id']}", json={"status": "rejected"}, headers=H), 400,
                     "'status' must be one of: submitted, approved, paid.")
        self.refused(self.c.patch(f"/api/hours/{c['id']}", json={"minutes": 60}, headers=H), 403,
                     "Only the person who claimed the hours can change those.")
        self.refused(self.c.patch(f"/api/hours/{c['id']}", json={}, headers=H), 400, "Nothing to change.")
        self.refused(self.c.patch("/api/hours/999", json={"status": "paid"}, headers=H), 404,
                     "That claim doesn't exist.")
        # an owner may delete any claim, whatever its status
        self.assertEqual(self.c.delete(f"/api/hours/{c['id']}", headers=H).json(), {"ok": True})
        self.assertEqual(self.c.get("/api/hours").json()["claims"], [])


if __name__ == "__main__":
    import unittest
    unittest.main()
