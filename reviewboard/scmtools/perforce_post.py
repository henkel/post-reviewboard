import re
import subprocess
import os
import sys
import time
import datetime


from tempfile import mkstemp, mkdtemp

from reviewboard.scmtools.perforce import PerforceTool
from reviewboard.scmtools.errors import SCMError, EmptyChangeSetError, ChangeSetError


class PerforcePostCommitTool(PerforceTool):
    name = "Perforce Post Commit" 
    support_post_commit = True

    EMPTY_FILE = '<FILE IS EMPTY>'
    DELETED_FILE = '<FILE IS DELETED>'

    
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
    
    def __init__(self, new_rev, p4_action):
        new_rev = int(new_rev)
        if new_rev > 0:
            self.first_rev   = new_rev - 1
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
            raise ChangeSetError('Please apply diff updates in sequential order and do not apply a diff twice')
        
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
                

# TODO refactor DiffStatus from perforce_post and svn_post into another file, e.g. PostCommitUtils  
class DiffFile:
    def __init__(self, name, description, data):
        self.name = name
        self.description = description
        self.data = data


    def read(self):
        return self.data


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

            modified_files = { }
            description = ''
            for changelist_no in changelist_numbers:
                description += self.merge_changelist_into_list_of_modified_files(changelist_no, modified_files)
                
            # Create temporary dir and files
            temp_dir_name = mkdtemp(prefix='reviewboard_perforce_post.')
            fd, empty_filename = mkstemp(dir=temp_dir_name)
            os.close(fd)
            fd, tmp_diff_from_filename = mkstemp(dir=temp_dir_name)
            os.close(fd)
            fd, tmp_diff_to_filename = mkstemp(dir=temp_dir_name)
            os.close(fd)
    
    
            cwd = os.getcwd()
        
            diff_lines = []
        
            for filename in modified_files:
                status = modified_files[filename]
                
                if status.first_rev == 0 and status.change_type == DiffStatus.DELETED:
                    # Skip added files that were deleted again
                    pass
                else: 
                    old_file, new_file = self._populate_temp_files(filename, status.first_rev,  status.last_rev, status.change_type,  False,  empty_filename,  tmp_diff_from_filename,  tmp_diff_to_filename)
                    diff_lines += self._diff_file(old_file, new_file, filename,  filename,  status.first_rev,  status.change_type,  cwd)

            # Clean-up
            os.unlink(empty_filename)
            os.unlink(tmp_diff_from_filename)
            os.unlink(tmp_diff_to_filename)
            os.rmdir(temp_dir_name)
            
            self.tool._disconnect()
            return DiffFile('summary TODO', description, ''.join(diff_lines))
        
        except Exception, e:
            self.tool._disconnect()
            raise SCMError('Error creating diff: ' + str(e) )


    def merge_changelist_into_list_of_modified_files(self, changelist_no, modified_files):
        try:
            changedesc = self.tool.p4.run_describe('-s', str(changelist_no))
        except Exception, e:
            raise SCMError('Perforce Error: ' + str(e))
                    
        if len(changedesc) == 0:
            raise SCMError('CL does not exist')

        changedesc = changedesc[0]
        
        if changedesc['status'] == 'pending':
            raise SCMError('pending CLs are not supported')

        try:
            changedesc['depotFile']
        except KeyError:
            return '' # skip CL

        for idx in range(0, len(changedesc['depotFile'])):
            path = changedesc['depotFile'][idx]
            
            if modified_files.has_key(path):
                modified_files[path].update(changedesc['rev'][idx], changedesc['action'][idx])
            else:
                modified_files[path] = DiffStatus(changedesc['rev'][idx], changedesc['action'][idx]) 
  

       # submit_time = time.ctime(int(changedesc['time']))
        
        submit_date = datetime.datetime.fromtimestamp(int(changedesc['time']))        
        time_str = submit_date.strftime("%Y/%m/%d %I:%M %p")

        description =  'Change ' + changedesc['change'] + ' by ' + changedesc['user'] + '@' + changedesc['client'] + ' on ' + time_str +'\n'
        description += '       ' + changedesc['desc']
        
        return description
    
    
    def _populate_temp_files(self,  depot_path, rev_first,  rev_last,  changetype,  cl_is_pending,  empty_filename,  tmp_diff_from_filename,  tmp_diff_to_filename):
        old_file = new_file =  empty_filename

        if changetype == DiffStatus.MODIFIED:
            # We have an old file, get p4 to take this old version from the
            # depot and put it into a plain old temp file for us
            self._write_file(depot_path, str(rev_first), tmp_diff_from_filename)
            old_file = tmp_diff_from_filename

            # Also print out the new file into a tmpfile
            self._write_file(depot_path, str(rev_last), tmp_diff_to_filename)
            new_file = tmp_diff_to_filename

        elif changetype == DiffStatus.ADDED:
            # We have a new file, get p4 to put this new file into a pretty
            # temp file for us. No old file to worry about here.
            self._write_file(depot_path, str(rev_last), tmp_diff_to_filename)
            new_file = tmp_diff_to_filename

        elif changetype == DiffStatus.DELETED:
            # We've deleted a file, get p4 to put the deleted file into  a temp
            # file for us. The new file remains the empty file.
            self._write_file(depot_path, str(rev_first), tmp_diff_from_filename)
            old_file = tmp_diff_from_filename
            
            f = open(tmp_diff_to_filename, "w")
            f.write(self.tool.DELETED_FILE)
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


    def _write_file(self, path, revision, tmpfile):
        """
        Grabs a file from Perforce and writes it to a temp file. We do this
        wrather than telling p4 print to write it out in order to work around
        a permissions bug on Windows.
        """
        data = self.tool.get_file(path, revision)
        f = open(tmpfile, "w")
        f.write(data)
        f.close()

    

