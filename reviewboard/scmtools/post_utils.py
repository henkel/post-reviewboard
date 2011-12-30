from reviewboard.reviews.models import ReviewRequest
from datetime import date, timedelta
from django.core.cache import cache


# Returns fresh (revision, user) tuples that are known to Review Board
def get_known_revisions(userid, repository, freshness_delta, extract_revision_user):
    # Filter fresh requests
    # Our fresh revisions cannot be contained in old requests!
    # We don't have to consider any ReviewRequest which were last updated before the shown user revisions were created.
    first_day = date.today()-freshness_delta
    query = ReviewRequest.objects.filter(last_updated__gte=first_day.strftime("%Y-%m-%d"))
    query = query.filter(repository__path__exact=repository)

    revisions = []
    for request in query:
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

    def __init__(self, cache_key_prefix, freshness_delta):
        self.cache_key_prefix = cache_key_prefix
        self.freshness_delta = freshness_delta


    def get_freshness_delta(self):
        return self.freshness_delta

    
    def get_scm_user(self, userid):
        cache_key = self.cache_key_prefix + '.post_scm_user.' + '.' + userid
        return cache.get(cache_key) or userid
        

    def set_scm_user(self, userid, scm_user):
        cache_key = self.cache_key_prefix + '.post_scm_user.' + '.' + userid
        cache.set(cache_key, scm_user, 30 * 3600 * 24) # expires after 1 month (max memcached expiration value)


    def get_latest_commits(self, userid, func_fetch_log_of_day):
        log_entries = self._get_latest_revision_log(func_fetch_log_of_day)
        user_revs = []

        lc_userid = userid.lower()

        for log in log_entries:
            if log[1].lower() == lc_userid:
                user_revs.append((log[0], log[2]))

        return user_revs


    def ignore_revisions(self, userid, new_revisions_to_be_ignored):
        cache_key = self.cache_key_prefix + '.post_ig.' + '.' + userid

        if new_revisions_to_be_ignored == None:
            cache.delete(cache_key)  # do not ignore any revisions any longer
            return

        if len(new_revisions_to_be_ignored) == 0:
            return

        all_to_be_ignored = cache.get(cache_key) or []
        all_to_be_ignored.append((date.today(), new_revisions_to_be_ignored))
        fresh_to_ignored = [ (creation, revs) for (creation, revs) in all_to_be_ignored if creation >= date.today()-self.freshness_delta ]

        cache.set(cache_key, fresh_to_ignored, self.freshness_delta.days * 3600 * 24 + self.freshness_delta.seconds)


    def get_ignored_revisions(self, userid):
        # Fetch revisions to be ignored
        cache_key = self.cache_key_prefix + '.post_ig.' + '.' + userid
        ignore_lists = [ revs for (_, revs) in cache.get(cache_key) or [] ]
        return [item for sublist in ignore_lists for item in sublist]


    def _get_latest_revision_log(self, func_fetch_log_of_day):
        cur = date.today()
        first_day = date.today()-self.freshness_delta
        log_entries = []
        while cur > first_day:
            latest_entries = log_entries
            log_entries = self._get_log_of_day(cur, func_fetch_log_of_day)
            log_entries.extend(latest_entries)
            cur -= timedelta(days=1)
        return log_entries


    def _get_log_of_day(self, day, func_fetch_log_of_day):
        if day == date.today():
            # Today - do not use cache
            return func_fetch_log_of_day(day)
        else:

            cache_key = self.cache_key_prefix + '.post_rc.'+ day.strftime("%Y-%m-%d")
            # Load through cache
            entries = cache.get(cache_key)
            if entries != None:
                return entries
            else:
                entries = func_fetch_log_of_day(day)
                cache.set(cache_key, entries, self.freshness_delta.days * 3600 * 24 + self.freshness_delta.seconds)
            return entries
