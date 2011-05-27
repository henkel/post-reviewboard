import time
import datetime
import urllib

from reviewboard.scmtools.post_utils import get_known_revisions

from datetime import datetime, date, timedelta

from reviewboard.scmtools.perforce_post import PerforcePostCommitTool, execute
from reviewboard.scmtools.errors import SCMError

from reviewboard.reviews.models import ReviewRequest

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

    def get_fields(self):
        fields = PerforcePostCommitTool.get_fields(self)
        #fields.append('revisions_choice')
        return fields
    
    def get_missing_revisions(self, userid):
        # Fetch user's commits from repository
        commits = self._get_latest_revisions(userid, self.freshness_delta)
        
        # Fetch the already contained
        known_revisions = get_known_revisions(userid, 
                                              self.repository, 
                                              self.freshness_delta, 
                                              extract_revision_user)
        
        # Fetch revisions to be ignored
        cache_key = 'perforce_post_tracker_ignore.'+ urllib.quote(self.repopath) +'.'+ userid
        ignore_lists = [ revs for (_, revs) in  cache.get(cache_key) or [] ]
        to_be_ignored = [item for sublist in ignore_lists for item in sublist]        
        
        # Revision exclusion predicate
        isExcluded = lambda rev : rev in known_revisions or rev in to_be_ignored
        
        return [ rev for rev in commits if not isExcluded(rev[0]) ]
    
    def get_filtered_changesets(self, userid):
        self._connect()
        
        try:
            # Get change list of last 30 days
            current_time = time.time()
            one_day_dur = 24*60*60
            since_time = current_time - 30 * one_day_dur
            since_date = datetime.datetime.fromtimestamp(since_time)        
            since_date_str = since_date.strftime("%Y/%m/%d")
            
            changes  = self.p4.run_changes('-l', '-s', 'submitted', '-u', userid, "//...@" + since_date_str + ",@now")
        
            changelists = []
            for changedesc in changes:
                no          = int(changedesc['change'])
                #submit_time = time.ctime(int(changedesc['time']))
                submit_date = datetime.datetime.fromtimestamp(int(changedesc['time']))        
                time_str = submit_date.strftime("%Y/%m/%d")
                #description =  'Change ' + changedesc['change'] + ' by ' + changedesc['user'] + '@' + changedesc['client'] + ' on ' + time_str + ': '+ unicode(changedesc['desc'])
                description = changedesc['change'] +' ___ on ' + time_str + ' in ' + changedesc['client'] + ' ___ ' +  changedesc['desc']
                changelists.append((no, description))
        
                changelists.sort(lambda x, y: cmp(x[0],  y[0]))
            return changelists
        
        except Exception, e:
            raise SCMError('Error creating diff: ' + str(e) )
