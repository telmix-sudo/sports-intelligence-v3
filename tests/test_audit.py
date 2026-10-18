import tempfile
import unittest
from pathlib import Path

from sports_intelligence.audit import audit_ledger


class AuditTests(unittest.TestCase):
    def test_ledger_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.csv"
            path.write_text("id,probability,odds,stake,result,pnl\n"
                            "1,0.65,1.50,10,WIN,5.0000\n"
                            "2,0.70,2.00,10,LOSS,-10.0000\n")
            report = audit_ledger(path)
            self.assertEqual(report["pnl_eur"], -5.0)
            self.assertEqual(report["bankroll_eur"], 995.0)

    def test_detects_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.csv"
            path.write_text("id,probability,odds,stake,result,pnl\n1,0.65,1.50,10,WIN,10\n")
            with self.assertRaises(ValueError):
                audit_ledger(path)


if __name__ == "__main__":
    unittest.main()
