# SVN post-commit SCM tool
# Author: Philipp Henkel, weltraumpilot@googlemail.com

from reviewboard.scmtools.errors import SCMError
from reviewboard.scmtools.svn import SVNTool
from reviewboard.scmtools.core import HEAD
import datetime
import os
import pysvn
import tempfile
import urllib

try:
    from pysvn import Revision, opt_revision_kind
except ImportError:
    pass

from django.core.cache import cache


class SVNPostCommitTool(SVNTool):
    name = "Subversion Post Commit"
    support_post_commit = True
    
    def __init__(self, repository):
        SVNTool.__init__(self, repository)
    
    def get_fields(self):
        return ['revisions']
    
    def get_diffs_use_absolute_paths(self):
        return True
    
    def get_file(self, path, revision=HEAD):
        return SVNTool.get_file(self, path, revision)

    def get_diff_file(self, revision_list):
        if revision_list == None or len(revision_list) == 0:
            raise SCMError('List of revisions is empty')
        return SVNDiffTool(self).get_diff_file(revision_list)
    
    def get_revision_info(self, revision):
        cache_key = 'svn_post_get_revision_info.'+ urllib.quote(self.repopath) +'.'+ str(revision)
        res = cache.get(cache_key)
        if res != None:
            return res
        
        rev = Revision(opt_revision_kind.number, revision)
        logs = self.client.log(self.repopath, rev, rev, True)
        
        if len(logs) != 1:
            raise SCMError('Revision ' + str(revision) +' not found')
        
        changed_paths = self._reduceToTextFiles(logs[0].changed_paths, revision)
        changes = self._normalize(changed_paths)
        
        revisionInfo = {'revision': revision, 
                        'user': logs[0].author or '', 
                        'description':logs[0].message or '', 
                        'changes':changes, 
                        'date':logs[0].date}
        
        cache.set(cache_key, revisionInfo)
        return revisionInfo
    
    def is_file(self, path, revision):
        quoted_path = urllib.quote(path)
        cache_key = 'svn_post_is_file.'+ urllib.quote(self.repopath) +'.'+ quoted_path   # revision is ignored because a change in file type is considered to happen only very seldom
        res = cache.get(cache_key)
        
        if res != None:
            return res
        
        rev = Revision(opt_revision_kind.number, revision)
        
        entry = self.client.info2(self.repopath+quoted_path, revision=rev)
        if entry[0][1]['kind'] != pysvn.node_kind.file:
            cache.set(cache_key, False)
            return False

        property = self.client.propget('svn:mime-type', self.repopath+quoted_path, rev)
        if len(property) > 0:
            if 'application/octet-stream' in property.values():
                cache.set(cache_key, False)
                return False
        
        cache.set(cache_key, True)    
        return True

    
    def _reduceToTextFiles(self, changed_paths, revision):
        filtered = []
        for cpath in changed_paths:
            rev = revision
            if cpath['action'] == 'D':
                rev = rev - 1
            if self.is_file(cpath['path'], rev):
                filtered.append(cpath) 
        return filtered
    
    
    def _normalize(self, changed_paths):
        normalized = []
        for cpath in changed_paths:
            normalized.append({'path':cpath['path'], 'action': cpath['action']} ) 
        return normalized
    
    
class DiffFile:
    def __init__(self, name, description, data):
        self.name = name
        self.description = description
        self.data = data

        
    def read(self):
        return self.data
    
# Requirement: Update DiffStatus in sequentially (order of change list numbers)
class DiffStatus:
    
    # Change types
    ADDED       = 'A'
    MODIFIED    = 'M'
    DELETED     = 'D'

    # Mapping of SVN actions to our change types
    MAP_ACTION_TO_CHANGE_TYPE = {'M': MODIFIED, # modified
                                 'D': DELETED,  # deleted
                                 'A': ADDED,    # added
                                 'R': MODIFIED, # modified
                             }
    
    def __init__(self, new_rev, svn_action):
        new_rev = int(new_rev)
        if new_rev > 0:
            self.first_rev   = new_rev - 1
        else:
            self.first_rev = 0
        
        self.last_rev    = new_rev
        self.change_type = self.MAP_ACTION_TO_CHANGE_TYPE[svn_action] 
        
        if self.change_type == self.ADDED:
            # first_rev has to be 0 to mark the file as completely new
            self.first_rev = 0
        
        
    def update(self, new_rev, svn_action):
        new_rev = int(new_rev)
        
        if (new_rev <= self.last_rev):
            raise SCMError('Please apply diff updates in order of change lists and do not apply a diff twice')
        
        self.last_rev = new_rev

        new_type = self.MAP_ACTION_TO_CHANGE_TYPE[svn_action]

        # ADDED
        if self.change_type == self.ADDED:
            if new_type == self.MODIFIED:
                pass                                    # Keep change type 'added' because file is still completely new 
            elif new_type == self.DELETED:
                self.change_type = self.DELETED
        
        # MODIFIED
        elif self.change_type == self.MODIFIED:
            if new_type == self.ADDED:
                pass                                    # Keep change type 'added' because file is still completely new
            elif new_type == self.DELETED:
                self.change_type = self.DELETED
        
        # DELETED
        elif self.change_type == self.DELETED:            
            if new_type == self.ADDED:                
                if self.first_rev == 0:
                    self.change_type = self.ADDED       # Keep 'added' because file is still completely new
                else:
                    self.change_type = self.MODIFIED    # Ignore delete if file was re-added
                    
            elif new_type == self.MODIFIED:             
                self.change_type = self.MODIFIED        # Ignore delete if file was re-added and is modified now
                



class SVNDiffTool:
    
    EMPTY_FILE = '<FILE IS EMPTY>'
    REMOVED_FILE = '<FILE WAS REMOVED>'

    
    def __init__(self, svn_tool):
        self.tool = svn_tool
        

    # Creates a diff file based on a SVN revisions
    def get_diff_file(self, revision_list):
        description = ''
        diff_lines = []

        try:
            revision_list.sort()
            
            if len(revision_list) != 1:
                summary = ''  # user should give a summary
            else:
                # Use commit message as summary
                revInfo = self.tool.get_revision_info(revision_list[0])
                desc = revInfo['description'].splitlines(True)
                if len(desc) > 0:
                    summary = desc[0].strip()
                

            # Determine list of modified files including a modification status
            modified_paths = {}
            for revision in revision_list:
                description += self._merge_revision_into_list_of_modified_files(revision, modified_paths)
            
            temp_dir_name = tempfile.mkdtemp(prefix='reviewboard_svn_post.')    
                
            # Create difference       
            for path in modified_paths:
                try:                 
                    status = modified_paths[path]
                

                    if status.change_type == DiffStatus.ADDED:
                        diff_lines += self._get_diff_of_new_file(path, status.last_rev)
    
                    elif status.change_type == DiffStatus.DELETED:
                        if status.first_rev != 0:  # we ignore new files that were deleted again
                            diff_lines += self._get_diff_of_deleted_file(path, status.first_rev)
                            
                    else: # MODIFIED
                        rev1 = Revision(opt_revision_kind.number, status.first_rev)
                        rev2 = Revision(opt_revision_kind.number, status.last_rev)
                        try:
                            diff = self.tool.client.diff(temp_dir_name, self.tool.repopath + urllib.quote(path), revision1=rev1, revision2=rev2)
                            if diff.startswith('Index:'):
                                # We have content changes
                                expanded_diff = self._expand_filename(diff, path, status.first_rev, status.last_rev)
                                self._remove_property_changes(expanded_diff) 
                                diff_lines += expanded_diff

                                
                        except pysvn.ClientError, e:
                            if str(e).find('was not found in the repository at revision') != -1:
                                # Looks like we have special case here, e.g. replacing and renaming at the same time
                                diff_lines += self._get_diff_of_new_file(path, status.last_rev)
                            else:
                                raise
                
                except Exception, e:
                    raise SCMError(' Problem with ' + path +': '+ str(e))
        
            os.rmdir(temp_dir_name)
            
        except Exception, e:
            raise SCMError(' Error creating diff: ' + str(e) )
        
        if len(diff_lines) < 3:
            raise SCMError(' There is no source code difference. The revision(s) might contain binary files and folders only or neutralize themselves.')
        
        return DiffFile(summary, description, str(''.join(diff_lines)))
    
    
    def _expand_filename(self, diff, fullname, rev1, rev2):
        difflines = diff.splitlines(True)
        
        if len(difflines) < 4:
            return difflines
        else:
            difflines[0] = 'Index: ' + fullname + '\n'
            difflines[2] = '--- ' + fullname + '\t(revision ' + str(rev1) + ')\n'
            difflines[3] = '+++ ' + fullname + '\t(revision ' + str(rev2) + ')\n'          
            return difflines
    
    
    def _remove_property_changes(self, diff_lines):        
        # Check diff lines from end to start (because property changes are appended at the end)
        for line_no in range(len(diff_lines)-1, 0, -1): 
            first_char = diff_lines[line_no][:1]
            if first_char == '+': # Heuristic: Stop early if real content diff is found
                return # do NOT change diff
            elif first_char == '-': # Heuristic: Stop early if real content diff is found
                return # do NOT change diff
            elif first_char == '_':
                if diff_lines[line_no-1].startswith('Property changes on:'):
                    # Remove property change info
                    for j in range(line_no-1, len(diff_lines)): 
                        diff_lines[j] = ''
                    return
                              
                
    def _get_diff_of_new_file(self, path, new_revision):
        # is same like diff with empty content
        content = self.tool.get_file(path, new_revision)
        diff_lines = content.splitlines(True)

        file_len = len(diff_lines)

        if not diff_lines[file_len - 1].endswith('\n'):
            diff_lines.append('\n\\ No newline at end of file\n')

        for idx in range(0, file_len):
            diff_lines[idx] = '+' + diff_lines[idx]
            
        diff_lines.insert(0, '@@ -0,0 +1,%d @@\n' % file_len)     # @@ -R +R @@ with R = l,s with l=line and s=block size in number of lines
        diff_lines.insert(0, '+++ %s\t(revision %s)\n' % (path, str(new_revision)))
        diff_lines.insert(0, '--- %s\t(revision 0)\n' % (path))
        
        return diff_lines
        
        
    def _get_diff_of_deleted_file(self, path, last_revision):
        # is same like diff with empty file
        content = self.tool.get_file(urllib.quote(path), last_revision)
        diff_lines = content.splitlines(True)

        file_len = len(diff_lines)
       
        if not diff_lines[file_len - 1].endswith('\n'):
            diff_lines.append('\n\\ No newline at end of file\n')

        for idx in range(0, file_len):
            diff_lines[idx] = '-' + diff_lines[idx]

        diff_lines.insert(0, '+' + self.REMOVED_FILE + '\n')
            
        diff_lines.insert(0, '@@ -1,%d +1,1 @@\n' % file_len)     # @@ -R +R @@ with R = l,s with l=line and s=block size in number of lines

        diff_lines.insert(0, '+++ %s\t(revision 0)\n' % (path))
        diff_lines.insert(0, '--- %s\t(revision %s)\n' % (path, str(last_revision)))
        
        return diff_lines   


    def _merge_revision_into_list_of_modified_files(self, revision, modified_files):

        revInfo = self.tool.get_revision_info(revision)
            
        if len(revInfo['changes']) == 0:
            return ''  # skip revision

        for change in revInfo['changes']:
            action = change['action']
            path = change['path']
            
            if modified_files.has_key(path):
                modified_files[path].update(revision, action)
            else:
                modified_files[path] = DiffStatus(revision, action) 
  
        submit_date = datetime.datetime.fromtimestamp(revInfo['date'])        
        time_str = submit_date.strftime("%Y-%m-%d %I:%M %p")

        description = str(revision) + ' by ' + revInfo['user'] + ' on ' + time_str +'\n'
        
        indent = ''
        for _ in range(1 + len(str(revision))):
            indent += ' '
            
        desclines = revInfo['description'].splitlines(True)
        for line in desclines:
            description += indent + line
        description += '\n\n'
        
        return description
    
    

