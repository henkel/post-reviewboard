# SVN post-commit SCM tool with tracking functionality
# Author: Philipp Henkel, weltraumpilot@googlemail.com

import pysvn
import time
import datetime
import urllib

from datetime import date, timedelta

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
        revisions_in_repository = self._get_latest_revisions(userid)
        revisions_in_reviewboard = get_reviewboards_changesets()
        return clean_key_value_list(revisions_in_repository, revisions_in_reviewboard)


    def _fetch_log_of_day_uncached(self, day):  
        start_time = time.mktime(day.timetuple())
        end_time = time.mktime((day+timedelta(days=1)).timetuple())
        
        start = Revision(opt_revision_kind.date, start_time)
        end = Revision(opt_revision_kind.date, end_time)
        log = []
        
        for entry in self.client.log(self.repopath, revision_start=start, revision_end=end):
            
            if entry['date'] < start_time:
                continue # workaround for pysvn bug which adds the previous day's last entry
            
            submit_date = datetime.datetime.fromtimestamp(entry['date'])      
            date_str = submit_date.strftime("%Y-%m-%d")
            desc = 'on ' +date_str + ' : ' +entry['revprops']['svn:log']
            log.append(( str(entry['revision'].number), 
                         entry['author'], 
                         desc))
        return log      
        
        
    def _fetch_log_of_day(self, day):

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
                cache.set(cache_key, entries)
            return entries
  
        
    def _fetch_latest_log(self, delta):
        cur = date.today()
        first_day = date.today()-delta
        log_entries = []
        while cur > first_day:
            latest_entries = log_entries
            log_entries = self._fetch_log_of_day(cur)
            log_entries.extend(latest_entries)
            cur -= timedelta(days=1)
        return log_entries
    
    
    def _get_latest_revisions(self, userid):
        log_entries = self._fetch_latest_log(timedelta(days=30)) # TODO timedelta should be customizable 
        user_revs = []
                                    
        try:
            for log in log_entries: 
                if log[1] == userid:
                    user_revs.append((log[0], log[2]))
               
        except pysvn.ClientError, e:
            raise SCMError(str(e))
        
        return user_revs


def get_reviewboards_changesets():
    changelists = []
    if True:
        return changelists  ## *********************************
    
    does_not_exist_counter = 0
    rid = 0

    while True:
        rid = rid + 1
        does_not_exist_counter = does_not_exist_counter + 1
    
        if does_not_exist_counter > 50:
            #debug('Stopped looking for more review requests at %d' % rid)
            break
    
        try:
            review_request =  ReviewRequest.objects.get(pk=rid)
            does_not_exist_counter = 0 # reset counter if we find a list

            if review_request.status == ReviewRequest.DISCARDED:
                # skip request
                continue
        
            # Parse change list number which comes like this "Change 457471 by phenkel@phenkel_reviewboard on 2009/08/04 16:03:33"
            desc = review_request.description 
            for line in desc.splitlines(True):
                cl = parse_review_request_description(line)
                if cl != None:
                    changelists.append(cl)
                    #debug('Found Perforce change list on Review Board: ' + str(cl))


        except Exception, e:
            pass
    
    # Sort and eliminate duplicates
    changelists = list(set(changelists))
    changelists.sort()

    return changelists
    
    
   
    

def parse_review_request_description(line):
    # Parse change list number which comes like this "Change 457471 by phenkel@phenkel_reviewboard on 2009/08/04 16:03:33"
    words = line.split(None,  2)
                
    if len(words)>=1 and words[0] == 'Change' and words[1].isdigit():
        return int(words[1])
    
    return None



# PRE: key_value_list and keys_to_be_removed are sorted (ascending)
# key_value_list is list <key, value> pairs, e.g. [(1, 'foo'), (2, 'bar')]
# keys_to_be_removed is list of keys, e.g. [1, 2, 3]
# keys_to_be_removed is allowed to contain keys not available in key_value_list
# POST: returns new key value list which does not contain any key listed in keys_to_be_removed
def clean_key_value_list(key_value_list, keys_to_be_removed):
    
    if keys_to_be_removed == None or len(keys_to_be_removed) == 0:
        # Nothing to be removed
        return key_value_list

    rm_idx = 0
    rm_key = keys_to_be_removed[rm_idx]  # at least one key is contained (see above checks)

    result = []

    # Iterate over key_value_list and build result list
    for key_value in key_value_list:
        key = key_value[0]
   
        if key < rm_key:
            # KEEP: key is less than next key that shall be removed 
            result.append( key_value ) 
    
        elif key == rm_key:
            # REMOVE: keys match
            pass
    
        else:  
            # key > rm_key
            
            # Continue to iterate over keys_to_be_removed till rm_key 
            # that is equal or greater than current key is found
            while rm_idx < len(keys_to_be_removed)-1:
                rm_idx = rm_idx+1
                rm_key = keys_to_be_removed[rm_idx]
                
                if key < rm_key:
                    # KEEP
                    result.append( key_value ) 
                    break
                elif key == rm_key:
                    # REMOVE
                    break
                else:
                    # SKIP: rm_key is not contained in key_value_list
                    pass
            
            # Check if 'end of keys_to_be_removed list was already reached'
            #          and 'key is greater than last rm_key' (other cases are covered in while loop)
            if rm_idx >= len(keys_to_be_removed)-1 and key > rm_key:
                result.append( key_value ) 

    return result







#
# Test code for function clean_key_value_list
# 

def test__clean_key_value_list():
    try:
        print '#### clean_key_value_list test suite'
        test1()
        test1_2()
        test2()
        test3()
        test4()
        test4_2();
        test5()
        test6()
        test7()
        test8()
        print '#### clean_key_value_list 100% PASSED'
    except Exception, e:
        print '#### clean_key_value_list tests FAILED' 
        pass

def test_equal(a, b):
    if len(a) != len(b):
        return False 
    for idx in range(0, len(a)):
        if a[idx] != b[idx]:
            return False
    return True


# Default case: more items in key_value_list than in keys_to_be_removed, every remove key available
def test1():
    print 'start test 1'
    a = [(1, '1'), (2, '2'), (3, '3'), (4, '4'), (5, '5'), (6, '6'), (7, '7')]
    b = [1, 2, 3, 4, 5]
    c = clean_key_value_list(a, b)
    d = [(6, '6'), (7, '7')]

    if not test_equal(c, d):
        raise Exception('test 1 failed')


# Mixed, some matches 
def test1_2():
    print 'start test 1_2'
    a = [(1, '1'), (2, '2'), (4, '4'), (5, '5'),(7, '7')]
    b = [1, 2, 3, 6 ]
    c = clean_key_value_list(a, b)
    d = [(4, '4'), (5, '5'),(7, '7')]

    if not test_equal(c, d):
        raise Exception('test 1_2 failed')


# More items in keys_to_be_removed    
def test2():
    print 'start test 2'
    a = [(1, '1')]
    b = [1, 2, 3, 4, 5]
    c = clean_key_value_list(a, b)
    d = []

    if not test_equal(c, d):
        raise Exception('test 2 failed')

# Empty key_value_list
def test3():
    print 'start test 3'
    a = []
    b = [1, 2, 3, 4, 5]
    c = clean_key_value_list(a, b)
    d = []

    if not test_equal(c, d):
        raise Exception('test 3 failed')

# Empty keys_to_be_removed list
def test4():
    print 'start test 4'
    a = [(1, '1'), (2, '2')]
    b = []
    c = clean_key_value_list(a, b)
    d = [(1, '1'), (2, '2')]

    if not test_equal(c, d):
        raise Exception('test 4 failed')

# Both lists empty
def test4_2():
    print 'start test 4_2'
    a = []
    b = []
    c = clean_key_value_list(a, b)
    d = []

    if not test_equal(c, d):
        raise Exception('test 4_2 failed')

# Keep last key_value_item
def test5():
    print 'start test 5'
    a = [(1, '1'), (2, '2')]
    b = [1]
    c = clean_key_value_list(a, b)
    d = [(2, '2')]

    if not test_equal(c, d):
        raise Exception('test 5 failed')

# Some key_values, some remove keys are missing
def test6():
    print 'start test 6'
    a = [(1, '1'), (2, '2'), (3, '3'), (4, '4'), (5, '5'), (6, '6')]
    b = [1, 2, 5, 6]
    c = clean_key_value_list(a, b)
    d = [(3, '3'), (4, '4')]

    if not test_equal(c, d):
        raise Exception('test 6 failed')


# Dieter's Bug (4 was doubled)
def test7():
    print 'start test 7'
    a = [(1, '1'), (2, '2'), (4, '4'), (5, '5'), (6, '6')]
    b = [1, 2, 3, 5 ]
    c = clean_key_value_list(a, b)
    d = [(4, '4'), (6, '6')]

    if not test_equal(c, d):
        raise Exception('test 7 failed')

# No match at all
def test8():
    print 'start test 8'
    a = [(4, '4')]
    b = [3, 5 ]
    c = clean_key_value_list(a, b)
    d = [(4, '4')]

    if not test_equal(c, d):
        raise Exception('test 8 failed')



