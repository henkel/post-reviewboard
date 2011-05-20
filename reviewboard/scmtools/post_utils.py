from datetime import date

from reviewboard.reviews.models import ReviewRequest

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
