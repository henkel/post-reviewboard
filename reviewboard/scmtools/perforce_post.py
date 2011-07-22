# Perforce post-commit SCM tool
# Author: Philipp Henkel, weltraumpilot@googlemail.com

import re
import subprocess
import os
import sys
import datetime
import urllib

from post_utils import DiffFile

from tempfile import mkstemp, mkdtemp

from reviewboard.scmtools.perforce import PerforceTool
from reviewboard.scmtools.errors import SCMError

from django.core.cache import cache

class PerforcePostCommitTool(PerforceTool):
    name = "Perforce Post Commit" 
    support_post_commit = True
    
    def __init__(self, repository):
        PerforceTool.__init__(self, repository)
        
    def get_fields(self):
        fields = PerforceTool.get_fields(self)
        fields.append('revisions')
        return fields
    
    def get_diff_file(self, change_numbers):
        if change_numbers == None or len(change_numbers) == 0:
            raise SCMError('List of changelist numbers is empty')
        diff_tool = PerforceDiffTool(self)
        return diff_tool.get_diff_file(change_numbers)
    
    def get_changedesc(self, change_number):
        cache_key = 'perforce_post_get_changedesc.'+ urllib.quote(str(self.repository.path)) +'.'+ str(change_number)
        res = cache.get(cache_key)
        if res != None:
            return res

        try:
            changedesc = self.p4.run_describe('-s', change_number)
        except Exception, e:
            raise SCMError('Perforce error: ' + str(e))
        
        if len(changedesc) == 0:
            raise SCMError('Change '+str(change_number)+ ' not found')

        changedesc = changedesc[0]
        
        cache.set(cache_key, changedesc, 60*60*24*7)
        return changedesc

        
# TODO refactor DiffStatus from perforce_post and svn_post into another file, e.g. PostCommitUtils  
# Requirement: Update DiffStatus in sequentially (order of change list numbers)
class DiffStatus:
    
    # Change types
    ADDED     = 'A'
    MODIFIED  = 'M'
    DELETED   = 'D'

    # Mapping of p4 actions to our change types
    MAP_ACTION_TO_CHANGE_TYPE = {'edit': MODIFIED,         # modified
                                 'integrate': MODIFIED,    # modified
                                 'add': ADDED,             # add
                                 'branch': ADDED,          # add
                                 'delete': DELETED,        # delete
                             }
    
    def __init__(self, new_rev, p4_action, old_rev=None):
        new_rev = int(new_rev)
        if old_rev != None:
            self.first_rew = int(old_rev)
        elif new_rev > 0:
            self.first_rev = new_rev - 1
        else:
            self.first_rev = 0
        
        self.last_rev    = new_rev
        self.change_type = self.MAP_ACTION_TO_CHANGE_TYPE[p4_action] 
        
        if self.change_type == self.ADDED:
            # first_rev has to be 0 to mark the file as completely new
            self.first_rev = 0
        
        
    def update(self, new_rev, p4_action):
        new_rev = int(new_rev)
        
        if (new_rev <= self.last_rev):
            raise SCMError('Please apply diff updates in sequential order and do not apply a diff twice')
        
        self.last_rev = new_rev

        new_type = self.MAP_ACTION_TO_CHANGE_TYPE[p4_action]

        # ADDED
        if self.change_type == self.ADDED:
            if new_type == self.MODIFIED:
                pass                                    # Keep change type 'add' because file is still completely new 
            elif new_type == self.DELETED:
                self.change_type = self.DELETED
        
        # MODIFIED
        elif self.change_type == self.MODIFIED:
            if new_type == self.ADDED:
                pass                                    # Keep change type 'add' because file is still completely new
            elif new_type == self.DELETED:
                self.change_type = self.DELETED
        
        # DELETED
        elif self.change_type == self.DELETED:            
            if new_type == self.ADDED:                
                if self.first_rev == 0:
                    self.change_type = self.ADDED       # Keep 'add' because file is still completely new
                else:
                    self.change_type = self.MODIFIED    # Ignore delete if file was re-added
                    
            elif new_type == self.MODIFIED:             
                self.change_type = self.MODIFIED        # Ignore delete if file was re-added and is modified now
                

def execute(command, env=None, split_lines=False, ignore_errors=False,
            extra_ignore_errors=()):
    """
    Utility function to execute a command and return the output.
    """

    if env:
        env.update(os.environ)
    else:
        env = os.environ.copy()

    env['LC_ALL'] = 'en_US.UTF-8'
    env['LANGUAGE'] = 'en_US.UTF-8'

    if sys.platform.startswith('win'):
        p = subprocess.Popen(command,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT,
                             shell=False,
                             universal_newlines=True,
                             env=env)
    else:
        p = subprocess.Popen(command,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT,
                             shell=False,
                             close_fds=True,
                             universal_newlines=True,
                             env=env)
    if split_lines:
        data = p.stdout.readlines()
    else:
        data = p.stdout.read()
    rc = p.wait()
    if rc and not ignore_errors and rc not in extra_ignore_errors:
        raise Exception('Failed to execute command: %s\n%s' % (command, data))

    return data



class PerforceDiffTool:
    def __init__(self, perforce_tool):
        self.tool = perforce_tool
        

    # Creates a diff file based on a Perforce change number list
    def get_diff_file(self, changelist_numbers):
        try:
            self.tool._connect()
            
            changelist_numbers.sort()

            if len(changelist_numbers) != 1:
                summary = ''  # user should give a summary
            else:
                # Use commit message as summary
                changedesc = self.tool.get_changedesc(changelist_numbers[0])
                desc = changedesc['desc'].splitlines(True)
                if len(desc) > 0:
                    summary = desc[0].strip()

            modified_files = { }
            description = ''
            shelved = False
            for changelist_no in changelist_numbers:
                desc, shelv = self.merge_changelist_into_list_of_modified_files(changelist_no, modified_files)
                description += desc
                shelved = shelved or shelv

            if shelved and len(changelist_numbers) != 1:
                raise SCMError('Shelved changelists can only be reviewed one at a time')
                
            # Create temporary dir and files
            temp_dir_name = mkdtemp(prefix='reviewboard_perforce_post.')
            fd, empty_filename = mkstemp(dir=temp_dir_name)
            os.close(fd)
            fd, tmp_diff_from_filename = mkstemp(dir=temp_dir_name)
            os.close(fd)
            fd, tmp_diff_to_filename = mkstemp(dir=temp_dir_name)
            os.close(fd)
    
            def cleanup():
                os.unlink(empty_filename)
                os.unlink(tmp_diff_from_filename)
                os.unlink(tmp_diff_to_filename)
                os.rmdir(temp_dir_name)
                self.tool._disconnect()
            
            try:
                cwd = os.getcwd()
        
                diff_lines = []
        
                for filename, status in modified_files.iteritems():
                    if status.change_type == DiffStatus.DELETED:
                        # Skip all files
                        pass
                    else: 
                        old_file, new_file = self._populate_temp_files(filename, status.first_rev,  status.last_rev, status.change_type,  shelved,  empty_filename,  tmp_diff_from_filename,  tmp_diff_to_filename)
                        diff_lines += self._diff_file(old_file, new_file, filename,  filename,  status.first_rev,  status.change_type,  cwd)

                cleanup()
                return DiffFile(summary, description, ''.join(diff_lines))
            except Exception, e:
                cleanup()
                raise
        
        except Exception, e:
            raise SCMError('Error creating diff: ' + str(e) )


    def merge_changelist_into_list_of_modified_files(self, changelist_no, modified_files):
        
        changedesc = self.tool.get_changedesc(str(changelist_no))
        
        shelved = 'shelved' in changedesc
        
        if changedesc['status'] == 'pending' and not shelved:
            raise SCMError('pending CLs are only supported if shelved')

        try:
            changedesc['depotFile']
        except KeyError:
            return '' # skip CL

        for idx in range(0, len(changedesc['depotFile'])):
            path = changedesc['depotFile'][idx]
            
            if modified_files.has_key(path):
                modified_files[path].update(changedesc['rev'][idx], changedesc['action'][idx])
            elif shelved:
                #for pending changelists, the "new" revision is the Changelist number and the old revision is in 'rev'
                modified_files[path] = DiffStatus(changedesc['shelved'], changedesc['action'][idx], changedesc['rev'][idx])
            else:
                #for normal changelists, the "new" revision is in "rev" and the old one is one less
                modified_files[path] = DiffStatus(changedesc['rev'][idx], changedesc['action'][idx]) 
   
        submit_date = datetime.datetime.fromtimestamp(int(changedesc['time']))        
        time_str = submit_date.strftime("%Y-%m-%d %I:%M %p")
        
        description = changedesc['change'] + ' by ' + changedesc['user'] + ' on ' + time_str + '\n'

        indent = ''.ljust(1 + len(changedesc['change']))
        desclines = changedesc['desc'].splitlines() 
        for line in desclines:
            description += indent + line.rstrip() + '\n'
        description += '\n'
                
        return (description, shelved)
    
    
    def _populate_temp_files(self,  depot_path, rev_first,  rev_last,  changetype,  cl_is_pending,  empty_filename,  tmp_diff_from_filename,  tmp_diff_to_filename):
        old_file = new_file =  empty_filename

        if changetype == DiffStatus.MODIFIED:
            # We have an old file, get p4 to take this old version from the
            # depot and put it into a plain old temp file for us
            self._write_file(depot_path, str(rev_first), tmp_diff_from_filename, False)
            old_file = tmp_diff_from_filename

            # Also print out the new file into a tmpfile
            self._write_file(depot_path, str(rev_last), tmp_diff_to_filename, cl_is_pending)
            new_file = tmp_diff_to_filename

        elif changetype == DiffStatus.ADDED:
            # We have a new file, get p4 to put this new file into a pretty
            # temp file for us. No old file to worry about here.
            self._write_file(depot_path, str(rev_last), tmp_diff_to_filename, cl_is_pending)
            new_file = tmp_diff_to_filename

        elif changetype == DiffStatus.DELETED:
            # We've deleted a file, get p4 to put the deleted file into  a temp
            # file for us. The new file remains the empty file.
            self._write_file(depot_path, str(rev_first), tmp_diff_from_filename, False)
            old_file = tmp_diff_from_filename
            
            f = open(tmp_diff_to_filename, "w")
            f.write('<FILE IS DELETED>')
            f.close()
            new_file = tmp_diff_to_filename

        else:
            raise Exception('Unexpected change type')        
        
        return old_file,  new_file


    def _diff_file(self, old_file, new_file,  local_name,  depot_path,  base_revision,  changetype,  cwd):
        diff_cmd = ["diff", "-urNp", old_file, new_file]
        # Diff returns "1" if differences were found.
        dl = execute(diff_cmd, extra_ignore_errors=(1,2)).splitlines(True)

        if local_name.startswith(cwd):
            local_path = local_name[len(cwd) + 1:]
        else:
            local_path = local_name

        # Special handling for the output of the diff tool on binary files:
        #     diff outputs "Files a and b differ"
        # and the code below expects the output to start with
        #     "Binary files "
        if len(dl) == 1 and \
            dl[0].startswith('Files %s and %s differ' %
                            (old_file, new_file)):
            dl = ['Binary files %s and %s differ\n'% (old_file, new_file)]

        if dl == [] or dl[0].startswith("Binary files "):
            if dl == []:
                print "Warning: %s in your changeset is unmodified or refers to a binary file" % local_path
            # Add our binary file header  
            dl.insert(0, "==== %s#%s ==%s== %s ====\n" % \
                        (depot_path, base_revision, changetype, local_path))                          
        else:
            m = re.search(r'(\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d)', dl[1])
            if m:
                timestamp = m.group(1)
            else:
                # Thu Sep  3 11:24:48 2007
                m = re.search(r'(\w+)\s+(\w+)\s+(\d+)\s+(\d\d:\d\d:\d\d)\s+(\d\d\d\d)', dl[1])
                if not m:
                    raise SCMError("Unable to parse diff header: %s" % dl[1])

                month_map = {
                    "Jan": "01",
                    "Feb": "02",
                    "Mar": "03",
                    "Apr": "04",
                    "May": "05",
                    "Jun": "06",
                    "Jul": "07",
                    "Aug": "08",
                    "Sep": "09",
                    "Oct": "10",
                    "Nov": "11",
                    "Dec": "12",
                }
                month = month_map[m.group(2)]
                day = m.group(3)
                timestamp = m.group(4)
                year = m.group(5)

                timestamp = "%s-%s-%s %s" % (year, month, day, timestamp)

            dl[0] = "--- %s\t%s#%s\n" % (local_path, depot_path, base_revision)
            dl[1] = "+++ %s\t%s\n" % (local_path, timestamp)
                
        return dl


    def _write_file(self, path, revision, tmpfile, from_changelist):
        """
        Grabs a file from Perforce and writes it to a temp file. We do this
        wrather than telling p4 print to write it out in order to work around
        a permissions bug on Windows.
        """
        data = self.tool.get_file(path, revision, from_changelist)
        f = open(tmpfile, "w")
        f.write(data)
        f.close()

    

