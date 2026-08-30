"""
Two invoices raised in the same second must not collide.

`Invoice.invoice_number` is `unique=True` and the timestamp in it only resolves
to the second, so the random tail carries the entire guarantee. At two bytes it
carried 65,536 values, which is a birthday problem rather than a safe margin:
fifty invoices inside one second collided about 1.9% of the time, a hundred
about 7.3%.

The cost of losing that bet is not a duplicate row. Subscription.save() creates
the invoice inside its own `transaction.atomic()` and nothing retries, so the
IntegrityError takes the subscription down with it and a customer whose M-Pesa
payment has just cleared is left with nothing.

Found on 2026-08-30 as an intermittent `duplicate key value violates unique
constraint "billing_invoice_invoice_number_key"` in the full suite — which
raises invoices in bursts inside setUp and so reaches those odds much sooner
than production does. It passed when its own test class was run alone, which is
exactly how a collision of this shape presents.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from billing.utils import generate_invoice_number

# Enough draws that two bytes is a certainty rather than a gamble: at 3,000 the
# old space of 65,536 collides with probability 1 - e^-68, i.e. every run. The
# widened space collides about four times in a million runs, which is quiet
# enough to keep in a suite that must be trusted when it goes red.
DRAWS = 3000


class FrozenSecond:
    """datetime.now() pinned, so every draw lands in the same second."""

    class _Now:
        def strftime(self, _fmt):
            return "20260830120000"

    @staticmethod
    def now():
        return FrozenSecond._Now()


class InvoiceNumberUniquenessTests(SimpleTestCase):

    def test_a_burst_inside_one_second_does_not_collide(self):
        """
        The real condition, not a proxy for it. Freezing the clock removes the
        one component that was doing any work by accident and leaves the random
        tail to carry the guarantee on its own.
        """
        with patch("billing.utils.datetime", FrozenSecond):
            numbers = [generate_invoice_number() for _ in range(DRAWS)]

        duplicates = len(numbers) - len(set(numbers))
        self.assertEqual(
            duplicates, 0,
            f"{duplicates} of {DRAWS} invoice numbers raised in the same "
            "second collided; a collision rolls back the subscription that "
            "was being created with it")

    def test_the_number_still_fits_the_column(self):
        """`invoice_number` is CharField(max_length=50)."""
        self.assertLessEqual(len(generate_invoice_number()), 50)

    def test_the_shape_is_unchanged(self):
        """
        Operators read these numbers aloud and paste them into searches, so the
        format is part of the contract even though the tail got longer.
        """
        number = generate_invoice_number()
        head, timestamp, tail = number.split("-")
        self.assertEqual(head, "INV")
        self.assertEqual(len(timestamp), 14)
        self.assertTrue(timestamp.isdigit())
        self.assertTrue(all(c in "0123456789ABCDEF" for c in tail),
                        f"tail {tail!r} is not uppercase hex")
