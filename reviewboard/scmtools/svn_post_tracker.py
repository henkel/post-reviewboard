# SVN post-commit SCM tool with revision tracking functionality
# Author: Philipp Henkel, weltraumpilot@googlemail.com

import pysvn
import time
import urllib

from datetime import datetime, date, timedelta
from operator import itemgetter

from reviewboard.scmtools.svn_post import SVNPostCommitTool
from reviewboard.scmtools.errors import SCMError
from reviewboard.scmtools.post_utils import get_known_revisions, RepositoryRevisionCache

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
        self.revisionCache = RepositoryRevisionCache('svn_post_tracker.'+ urllib.quote(self.repopath), 
                                                     self.freshness_delta, 
                                                     self._fetch_log_of_day_uncached)
    
    
    def get_fields(self):
        fields = SVNPostCommitTool.get_fields(self)
        fields.append('revisions_choice')
        return fields
    
    
    def get_missing_revisions(self, userid):
        # Fetch user's commits from repository
        commits = self.revisionCache.get_latest_commits(userid)
        
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
        
        sorted_revisions = sorted([ rev for rev in commits if not isExcluded(rev[0]) ], 
                                  key=itemgetter(0), 
                                  reverse=False) 
        return sorted_revisions


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
        
        try:
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
                       
        except pysvn.ClientError, e:
            raise SCMError('Error fetching revisions: ' +str(e))
        
        return log      
        
        

