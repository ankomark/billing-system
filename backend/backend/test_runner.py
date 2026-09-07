"""
The test runner, which exists to turn one production setting off.

SECURE_SSL_REDIRECT is on whenever DEBUG is off, which is every deployed
environment and therefore every container anyone runs the suite in. Django's
test client speaks plain HTTP, so SecurityMiddleware answered every request
with a 301 to an https URL and the view under test never ran at all. The
assertion that followed compared 301 against whatever was expected, or reached
for `.data` on an HttpResponsePermanentRedirect and raised AttributeError.

That was 578 of the 654 failures in this suite -- 269 of the AttributeError and
309 of the `301 != something`. Not one of them was a defect in the code being
tested: the suite had been red for so long that its output carried no
information either way, which is the expensive part. A red suite that is
expected to be red cannot tell anybody they broke something.

Turned off here rather than in settings, and rather than by making several
hundred test requests pass secure=True:

  * settings would need to guess it is being tested, usually by looking for
    "test" in sys.argv. That is a guess about the command line, made in the one
    file whose correctness everything else rests on, and it is wrong for any
    runner invoked differently.

  * secure=True at each call site is the same fact restated several hundred
    times, and every test written afterwards has to remember it. The one added
    with this change forgot, and its failure looked exactly like a bug in the
    view rather than the boilerplate it actually was.

Nothing is lost by not exercising the redirect. It is Django's middleware
acting on a Django setting; the behaviour belongs to Django's own tests, and
production still redirects because production does not run this runner.

Everything else about the run is DiscoverRunner's.
"""

from django.conf import settings
from django.test.runner import DiscoverRunner


class BillingTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)

        # Before the first request, and therefore before ClientHandler builds
        # its middleware chain -- SecurityMiddleware reads this once in its
        # __init__ and keeps it, so setting it any later than this would have
        # no effect on a handler that had already answered a request.
        settings.SECURE_SSL_REDIRECT = False
