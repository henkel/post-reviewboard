# Perforce post-commit SCM tool with revision tracking functionality
# Author: Philipp Henkel, weltraumpilot@googlemail.com

import urllib

from datetime import datetime, timedelta
from operator import itemgetter

from reviewboard.scmtools.perforce_post import PerforcePostCommitTool
from reviewboard.scmtools.errors import SCMError
from reviewboard.scmtools.post_utils import get_known_revisions, RepositoryRevisionCache


def extract_revision_user(line):
    # Try to extract revision info tuple (rev, user, line, shelved) from line
    # Revision info example: "116855 by henkel on 2011-03-24 11:30 AM"
    words = line.split(' ',  4)

    if len(words)>=4 and words[0].isdigit() and words[1] == 'by' and words[3] == 'on':
        return (words[0], words[2], line, False)
    
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
        
        #first compare by shelved or not (pos 3 in tuple), then by changenumber (pos 0 in tuple)
        sorted_revisions = sorted([ rev for rev in commits if not isExcluded(rev[0]) ], 
                                  key=itemgetter(0), 
                                  reverse=False)
        
        #don't cache Shelved changelists
        sorted_shelved = sorted([(rev[0], rev[2]) for rev in self._fetch_log_of_day_uncached(None, True, userid.lower())], key=itemgetter(0))
        
        # Starting with oldest entries first, return first the submitted revisions, then the shelved 
        # changelists because these are considered brand new
        return sorted_revisions + sorted_shelved
    
    
    def ignore_revisions(self, userid, new_revisions_to_be_ignored):
        self.revisionCache.ignore_revisions(userid, new_revisions_to_be_ignored)
        
    
    def _fetch_log_of_day_uncached(self, day, shelved=False, userid=None):  
        self._connect()
        log = []
        
        try:
            if shelved:
                #shelved changes. Note: those have a key 'shelved': ''
                #ignore day
                changes = self.p4.run_changes('-l', '-s', 'shelved', '-u', userid)
            else:
                #submitted changes
                day_plus_one = day + timedelta(days=1)
                changes = self.p4.run_changes('-l', '-s', 'submitted', '@' + day.strftime("%Y/%m/%d") + ',' + day_plus_one.strftime("%Y/%m/%d"))

            for changedesc in changes:
                submit_date = datetime.fromtimestamp(int(changedesc['time']))        
                date_str = submit_date.strftime("%Y-%m-%d")
                
                msg = changedesc['desc'].splitlines()[0].strip()
                shelved = 'shelved' in changedesc
                #' by ' + changedesc['user'] + 
                desc = ('shelved ' if shelved else 'on ' + date_str)  + ' : ' + msg
                log.append(( str(changedesc['change']), 
                             changedesc['user'], 
                             desc,
                             shelved ))
        except Exception, e:
            raise SCMError('Error fetching revisions: ' +str(e))
        
        return log
