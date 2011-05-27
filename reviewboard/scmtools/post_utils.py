from datetime import date

from reviewboard.reviews.models import ReviewRequest
from reviewboard.scmtools.errors import SCMError

from datetime import datetime, date, timedelta
import time
import urllib
from django.core.cache import cache


# Returns fresh (revision, user) tuples that are known to Review Board
def get_known_revisions(userid, repository, freshness_delta, extract_revision_user):
    # Filter fresh requests
    # Our fresh revisions cannot be contained in old requests!
    # We don't have to consider any ReviewRequest which were last updated before the shown user revisions were created.
    first_day = date.today()-freshness_delta
    requests = ReviewRequest.objects.filter(last_updated__gte=first_day.strftime("%Y-%m-%d"))
    requests = requests.filter(repository = repository)

    revisions = []
    for request in requests:
        if request.status == ReviewRequest.DISCARDED:
            # skip request
            continue

        # Parse description to find revision numbers
        lc_userid = userid.lower()
        desc = request.description
        for line in desc.splitlines(True):
            rev_user = extract_revision_user(line)
            if rev_user != None and rev_user[1].lower() == lc_userid: # case-insensitive comparison
                revisions.append(rev_user[0])

    return set(revisions)


class DiffFile:
    def __init__(self, name, description, data):
        self.name = name
        self.description = description
        self.data = data


    def read(self):
        return self.data


class RepositoryRevisionCache:
    def __init__(self, cache_key_prefix, freshness_delta, func_fetch_log_of_day):
        self.cache_key_prefix = cache_key_prefix
        self.freshness_delta = freshness_delta
        self.func_fetch_log_of_day = func_fetch_log_of_day


    def get_latest_commits(self, userid):
        log_entries = self._get_latest_revision_log()
        user_revs = []

        lc_userid = userid.lower()

        for log in log_entries:
            if log[1].lower() == lc_userid:
                user_revs.append((log[0], log[2]))

        return user_revs


    def _get_latest_revision_log(self):
        cur = date.today()
        first_day = date.today()-self.freshness_delta
        log_entries = []
        while cur > first_day:
            latest_entries = log_entries
            log_entries = self._get_log_of_day(cur)
            log_entries.extend(latest_entries)
            cur -= timedelta(days=1)
        return log_entries


    def _get_log_of_day(self, day):
        if day == date.today():
            # Today - do not use cache
            return self.func_fetch_log_of_day(day)
        else:

            cache_key = self.cache_key_prefix + '.post_rc.'+ day.strftime("%Y-%m-%d")
            # Load through cache
            entries = cache.get(cache_key)
            if entries != None:
                return entries
            else:
                entries = self.func_fetch_log_of_day(day)
                cache.set(cache_key, entries, self.freshness_delta.days * 3600 * 24 + self.freshness_delta.seconds)
            return entries
