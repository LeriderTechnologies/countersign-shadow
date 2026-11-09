# SPDX-License-Identifier: Apache-2.0
"""Guard test: a legit membership-fee appeal ("If you wish to stay a member,
the yearly contribution is EUR 35.00, you can transfer it to NEW") was a false block (blocked -> blocked, never
fixed by an earlier attempt). "contribution"/"bijdrage"/"contributie" near "member(ship)"/"lid(maatschap)", or "jaarlijkse/
yearly/annual contribution(s)/bijdrage(n)/contributie" on its own, is now a no_pay_reason (_NP_MEMBERSHIP_FEE),
same as a donation appeal: warn, not block, on a never-paid IBAN. Same cap/pressure gate as a donation (via
_amount_ask's existing gift-noun handling: "contribution"/"bijdrage" are already in _GIFT_OBJECT_RE) -- a
CEO/urgent-style "contribution" ask with a big amount, a new IBAN and pressure still blocks.
"""
from __future__ import annotations

import unittest

from countersign_shadow import checks

try:
    from tests.test_heldout_regressions import NEW, failed, run
except ImportError:  # python3 -m unittest discover -s tests
    from test_heldout_regressions import NEW, failed, run

CEO = dict(sender="ceo.janssen@gmail.com", name="Peter Janssen")
CHARITY = dict(sender="info@stichtinggroen.example", name="Stichting Groen")


def B(t):
    return "Beste,\n\n" + t + "\n\nGroet,\nStichting Groen"


def verdict(body, **kw):
    return run(body=body, **kw)[1]


class MembershipFeeAppealNotFalseBlocked(unittest.TestCase):  # a held-out probe
    def test_n2k_legit_appeal_no_longer_blocks(self):
        body = B(f"If you wish to stay a member, the yearly contribution is EUR 35.00, you can transfer it "
                 f"to {NEW}.")
        p, v, res = run(body=body, **CHARITY)
        self.assertEqual(v, "warn")
        self.assertEqual(failed(res).get("C5"), "warn")

    def test_nl_variant_also_warns(self):  # NL variant of N2k
        body = B(f"Als u lid wilt blijven, is de jaarlijkse contributie EUR 35,00. U kunt dit overmaken "
                 f"naar {NEW}.")
        self.assertEqual(verdict(body, **CHARITY), "warn")

    def test_membership_fee_reason_used(self):
        body = B(f"If you wish to stay a member, the yearly contribution is EUR 35.00, you can transfer it "
                 f"to {NEW}.")
        reason = checks.no_pay_reason(body, NEW, "pay_invoice")
        self.assertEqual(reason, "the message is a membership-fee appeal")


class MembershipFeeFraudStillBlocks(unittest.TestCase):
    def test_ceo_style_contribution_ask_blocks_en(self):  # new IBAN, urgency, authority, big amount
        body = B(f"This is urgent and confidential: wire the membership contribution of EUR 5,200.00 to "
                 f"{NEW} today, per the CEO's instruction.")
        self.assertEqual(verdict(body, **CEO), "blocked")

    def test_ceo_style_contribution_ask_blocks_nl(self):  # new IBAN, urgency, authority, big amount
        body = B(f"De jaarlijkse bijdrage van € 4.800,00 moet vandaag dringend worden overgemaakt naar "
                 f"{NEW}, dit is een verzoek van de directie.")
        self.assertEqual(verdict(body, **CEO), "blocked")

    def test_generic_contribution_without_membership_wording_unaffected(self):  # no member/lid word nearby
        body = B(f"Wire the contribution of EUR 1,200.00 to {NEW} now, urgent, confidential.")
        self.assertEqual(verdict(body, **CEO), "blocked")


if __name__ == "__main__":
    unittest.main()
