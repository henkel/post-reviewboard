import time
import datetime
from datetime import datetime, date, timedelta

from reviewboard.scmtools.perforce_post import PerforcePostCommitTool, execute
from reviewboard.scmtools.errors import SCMError

from reviewboard.reviews.models import ReviewRequest



class PerforcePostCommitTrackerTool(PerforcePostCommitTool):
    name = "Perforce Post Commit Tracker"
    support_post_commit_tracking = True
    
    freshness_delta = timedelta(days=21)
    
    def __init__(self, repository):
        PerforcePostCommitTool.__init__(self, repository)

    def get_fields(self):
        fields = PerforcePostCommitTool.get_fields(self)
        fields.append('revisions_choice')
        return fields
    
    
    def get_missing_changesets(self, userid):
        #test__clean_key_value_list()
        cl_list_perforce = self.get_filtered_changesets(userid)
        cl_list_reviewboard = get_reviewboards_changesets()
        return clean_key_value_list(cl_list_perforce, cl_list_reviewboard)

    
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
        
        

def get_reviewboards_changesets():
    changelists = []
    
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


        except Exception:
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