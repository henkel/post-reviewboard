# SVN post-commit SCM tool with revision tracking functionality
# Author: Philipp Henkel, weltraumpilot@googlemail.com

import pysvn
import time
import urllib

from datetime import datetime, date, timedelta

from reviewboard.scmtools.svn_post import SVNPostCommitTool
from reviewboard.scmtools.errors import SCMError
from reviewboard.reviews.models import ReviewRequest


try:
    from pysvn import Revision, opt_revision_kind
except ImportError:
    pass

from django.core.cache import cache



class SVNPostCommitTrackerTool(SVNPostCommitTool):
    name = "Subversion Post Commit Tracker"
    support_post_commit_tracking = True
    
    def __init__(self, repository):
        SVNPostCommitTool.__init__(self, repository)
    
    
    def get_fields(self):
        return ['revisions', 'revisions_choice']
    
    
    def get_missing_revisions(self, userid):
        freshness_delta = timedelta(days=21)
        
        # Fetch user's commits from repository
        revisions_in_repository = self._get_latest_revisions(userid, freshness_delta)
        
        # Fetch the already contained
        revision_numbers_in_reviewboard = get_latest_revisions_added_to_reviewboard(userid, freshness_delta)
        
        return [ rev for rev in revisions_in_repository if not rev[0] in revision_numbers_in_reviewboard ]


    def _fetch_log_of_day_uncached(self, day):  
        start_time = time.mktime(day.timetuple())
        end_time = time.mktime((day+timedelta(days=1)).timetuple())
        
        start = Revision(opt_revision_kind.date, start_time)
        end = Revision(opt_revision_kind.date, end_time)
        log = []
        
        for entry in self.client.log(self.repopath, revision_start=start, revision_end=end):
            
            if entry['date'] < start_time:
                continue # workaround for pysvn bug which adds the previous day's last entry
            
            submit_date = datetime.fromtimestamp(entry['date'])      
            date_str = submit_date.strftime("%Y-%m-%d")
            desc = 'on ' +date_str + ' : ' +entry['revprops']['svn:log']
            log.append(( str(entry['revision'].number), 
                         entry['author'], 
                         desc))
        return log      
        
        
    def _fetch_log_of_day(self, day, freshness_delta):

        if day == date.today():
            # Today - do not use cache
            return self._fetch_log_of_day_uncached(day)
        else:
            # Load through cache
            cache_key = 'svn_post_tracker_log.'+ urllib.quote(self.repopath) +'.'+ day.strftime("%Y-%m-%d")
            entries = cache.get(cache_key)
            if entries != None:
                return entries
            else:
                entries = self._fetch_log_of_day_uncached(day)
                cache.set(cache_key, entries, freshness_delta.days * 3600 + freshness_delta.seconds)
            return entries
  
        
    def _fetch_latest_log(self, freshness_delta):
        cur = date.today()
        first_day = date.today()-freshness_delta
        log_entries = []
        while cur > first_day:
            latest_entries = log_entries
            log_entries = self._fetch_log_of_day(cur, freshness_delta)
            log_entries.extend(latest_entries)
            cur -= timedelta(days=1)
        return log_entries
    
    
    def _get_latest_revisions(self, userid, freshness_delta):
        log_entries = self._fetch_latest_log(freshness_delta) 
        user_revs = []
                                    
        try:
            for log in log_entries: 
                if log[1] == userid:
                    user_revs.append((log[0], log[2]))
               
        except pysvn.ClientError, e:
            raise SCMError('Error fetching revisions: ' +str(e))
        
        return user_revs
    
    
def get_latest_revisions_added_to_reviewboard(userid, freshness_delta):
    
    # Filter fresh requests
    # Our fresh revisions cannot be contained in old requests!
    # We don't have to consider any ReviewRequest which were last updated before the shown user revisions were created.
    first_day = date.today()-freshness_delta
    requests = ReviewRequest.objects.filter(last_updated__gte=first_day.strftime("%Y-%m-%d"))
    
    revisions = []
    for request in requests:
        if request.status == ReviewRequest.DISCARDED:
            # skip request
            continue
        
        # Parse description to find revision numbers
        lc_userid = userid.lower()
        desc = request.description 
        for line in desc.splitlines(True):
            rev_user = parse_review_request_description(line)
            if rev_user != None and rev_user[1].lower() == lc_userid: # case-insensitive comparison
                revisions.append(rev_user[0])
     
    return set(revisions)
    

def parse_review_request_description(line):
    # Try to extract revision info tuple (rev, user) from line
    # Revision info example: "116855 by henkel on 2011-03-24 11:30 AM"
    words = line.split(' ',  4)
                
    if len(words)>=4 and words[0].isdigit() and words[1] == 'by' and words[3] == 'on':
        return (words[0], words[2])
    
    return None

