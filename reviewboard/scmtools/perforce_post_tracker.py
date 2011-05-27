# Perforce post-commit SCM tool with revision tracking functionality
# Author: Philipp Henkel, weltraumpilot@googlemail.com

import urllib

from datetime import datetime, date, timedelta
from operator import itemgetter

from reviewboard.reviews.models import ReviewRequest

from reviewboard.scmtools.perforce_post import PerforcePostCommitTool
from reviewboard.scmtools.errors import SCMError
from reviewboard.scmtools.post_utils import get_known_revisions, RepositoryRevisionCache

from django.core.cache import cache

def extract_revision_user(line):
    # Try to extract revision info tuple (rev, user) from line
    # Revision info example: "116855 by henkel on 2011-03-24 11:30 AM"
    words = line.split(' ',  4)
                
    if len(words)>=4 and words[0].isdigit() and words[1] == 'by' and words[3] == 'on':
        return (words[0], words[2])
    
    return None


class PerforcePostCommitTrackerTool(PerforcePostCommitTool):
    name = "Perforce Post Commit Tracker"
    
    freshness_delta = timedelta(days=21)
    
    def __init__(self, repository):
        PerforcePostCommitTool.__init__(self, repository)
        self.revisionCache = RepositoryRevisionCache('perforce_post_tracker.'+ urllib.quote(str(self.repository.path)), 
                                                     self.freshness_delta, 
                                                     self._fetch_log_of_day_uncached)

    def get_fields(self):
        fields = PerforcePostCommitTool.get_fields(self)
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

        commits_to_be_ignored = self.revisionCache.get_ignored_revisions(userid)           
        
        # Revision exclusion predicate
        isExcluded = lambda rev : rev in known_revisions or rev in commits_to_be_ignored
        
        sorted_revisions = sorted([ rev for rev in commits if not isExcluded(rev[0]) ], 
                                  key=itemgetter(0), 
                                  reverse=False) 
        return sorted_revisions
    
    
    def ignore_revisions(self, userid, new_revisions_to_be_ignored):
        self.revisionCache.ignore_revisions(userid, new_revisions_to_be_ignored)
        
    
    def _fetch_log_of_day_uncached(self, day):  
        self._connect()
        log = []
        
        try:
            day_plus_one = day + timedelta(days=1)
            changes  = self.p4.run_changes('-l', '-s', 'submitted', '@' + day.strftime("%Y/%m/%d") + ',' + day_plus_one.strftime("%Y/%m/%d"))

            for changedesc in changes:
                submit_date = datetime.fromtimestamp(int(changedesc['time']))        
                date_str = submit_date.strftime("%Y/%m/%d")
                
                msg = changedesc['desc'].splitlines()[0].strip()
                desc = 'on ' +date_str + ' : ' + msg
                log.append(( int(changedesc['change']), 
                             changedesc['user'], 
                             desc))
        except Exception, e:
            raise SCMError('Error fetching revisions: ' +str(e))
        
        return log
