# SVN post-commit SCM tool with revision tracking functionality
# Author: Philipp Henkel, weltraumpilot@googlemail.com

import pysvn
import time
import urllib

import post_utils

from datetime import datetime, date, timedelta

from reviewboard.scmtools.svn_post import SVNPostCommitTool
from reviewboard.scmtools.errors import SCMError
from reviewboard.reviews.models import ReviewRequest
from reviewboard.scmtools.post_utils import get_known_revisions

try:
    from pysvn import Revision, opt_revision_kind
except ImportError:
    pass

from django.core.cache import cache



def extract_revision_user(line):
    # Try to extract revision info tuple (rev, user) from line
    # Revision info example: "116855 by henkel on 2011-03-24 11:30 AM"
    words = line.split(' ',  4)
                
    if len(words)>=4 and words[0].isdigit() and words[1] == 'by' and words[3] == 'on':
        return (words[0], words[2])
    
    return None


class SVNPostCommitTrackerTool(SVNPostCommitTool):
    name = "Subversion Post Commit Tracker"
    
    freshness_delta = timedelta(days=21)
    
    def __init__(self, repository):
        SVNPostCommitTool.__init__(self, repository)
    
    
    def get_fields(self):
        fields = SVNPostCommitTool.get_fields(self)
        fields.append('revisions_choice')
        return fields
    
    
    def get_missing_revisions(self, userid):
        # Fetch user's commits from repository
        commits = self._get_latest_commits(userid, self.freshness_delta)
        
        # Fetch the already contained
        known_revisions = get_known_revisions(userid, 
                                              self.repository, 
                                              self.freshness_delta, 
                                              extract_revision_user)
        
        # Fetch revisions to be ignored
        cache_key = 'svn_post_tracker_ignore.'+ urllib.quote(self.repopath) +'.'+ userid
        ignore_lists = [ revs for (_, revs) in  cache.get(cache_key) or [] ]
        to_be_ignored = [item for sublist in ignore_lists for item in sublist]        
        
        # Revision exclusion predicate
        isExcluded = lambda rev : rev in known_revisions or rev in to_be_ignored
        
        return [ rev for rev in commits if not isExcluded(rev[0]) ]


    def ignore_revisions(self, userid, new_revisions_to_be_ignored):
        cache_key = 'svn_post_tracker_ignore.'+ urllib.quote(self.repopath) +'.'+ userid
        
        if new_revisions_to_be_ignored == None:
            cache.delete(cache_key)  # do not ignore any revisions any longer
            return
        
        if len(new_revisions_to_be_ignored) == 0:
            return

        all_to_be_ignored = cache.get(cache_key) or []
        all_to_be_ignored.append((date.today(), new_revisions_to_be_ignored))
        fresh_to_ignored = [ (creation, revs) for (creation, revs) in all_to_be_ignored if creation >= date.today()-self.freshness_delta ]
        
        cache.set(cache_key, fresh_to_ignored, self.freshness_delta.days * 3600 * 24 + self.freshness_delta.seconds)
        

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
            
            message = entry['message'] or '' 
            msg = message.splitlines()[0].strip()
            desc = 'on ' +date_str + ' : ' + msg
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
                cache.set(cache_key, entries, freshness_delta.days * 3600 * 24 + freshness_delta.seconds)
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
    
    
    def _get_latest_commits(self, userid, freshness_delta):
        log_entries = self._fetch_latest_log(freshness_delta) 
        user_revs = []
                
        lc_userid = userid.lower()
                            
        try:
            for log in log_entries: 
                if log[1].lower() == lc_userid:
                    user_revs.append((log[0], log[2]))
               
        except pysvn.ClientError, e:
            raise SCMError('Error fetching revisions: ' +str(e))
        
        return user_revs



