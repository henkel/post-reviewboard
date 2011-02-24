

from reviewboard.scmtools.errors import SCMError, ChangeSetError, \
    EmptyChangeSetError
from reviewboard.scmtools.svn import SVNTool
from scmtools.core import ChangeSet
import datetime
import os
import pysvn
import tempfile


try:
    from pysvn import Revision, opt_revision_kind
except ImportError:
    pass



class SVNPostCommitTool(SVNTool):
    support_post_commit = True
    
    def __init__(self, repository):
        SVNTool.__init__(self, repository)
    
    def get_fields(self):
        return ['change_numbers']
    
    def get_diffs_use_absolute_paths(self):
        return True
    
    def get_changeset(self, changesetid):
        # TODO is this function really needed????
        rev = Revision(opt_revision_kind.number, changesetid)
        logs = self.client.log(self.repopath, rev, rev, True)
        
        if len(logs) != 1:
            return None

        changeset = ChangeSet()
        changeset.changenum = changesetid
        changeset.username = logs[0].author
        changeset.description = logs[0].message or ''
        changeset.summary = changeset.description.split('\n', 1)[0]  # summary is first line of description
        # TODO files plus actions changeset.files =      logs[0]['changed_paths']]
        
        return changeset


    def get_diff_file(self, revision_list):
        if revision_list == None or len(revision_list) == 0:
            raise ChangeSetError('List of revisions is empty')
        return SVNDiffTool(self).get_diff_file(revision_list)
    
    
    
class DiffFile:
    def __init__(self, data, description):
        self.data = data
        self.name = 'SVN Post Commit Diff' 
        self.description = description
        
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
            raise ChangeSetError('Please apply diff updates in order of change lists and do not apply a diff twice')
        
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
        description = u''
        diff_lines = []

        try:
            revision_list.sort()

            # Determine list of modified files including a modification status
            modified_paths = {}
            for revision in revision_list:
                description += unicode(self.merge_revision_into_list_of_modified_files(revision, modified_paths))
            
            temp_dir_name = tempfile.mkdtemp(prefix='reviewboard_svn_post.')    
                
            # Create difference       
            for path in modified_paths:
                try:                 
                    status = modified_paths[path]
                
                    if status.change_type == DiffStatus.ADDED:
                        diff_lines += self._get_diff_of_new_file(path, status.last_rev)
    
                    elif status.change_type == DiffStatus.DELETED:
                        diff_lines += self._get_diff_of_deleted_file(path, status.first_rev)
                            
                    else: # MODIFIED
                        rev1 = Revision(opt_revision_kind.number, status.first_rev)
                        rev2 = Revision(opt_revision_kind.number, status.last_rev)
                        url = self.tool.repopath + path
                        diff = self.tool.client.diff(temp_dir_name, url, revision1=rev1, revision2=rev2)        
                        #res = self.tool.execute_command_in_workspace(['difference', '/version:C' + str(status.first_rev) + '~C' + str(status.last_rev), filename])
                        #enc = self.tool.get_file_encoding(path, str(status.last_rev)) 
                        #res = unicode(res, enc, errors='replace')
                        #diff_lines += self._patch_diff_header(res.splitlines(True), path, status.first_rev, status.change_type)
                        expanded_diff = self._expand_filename(diff, path, status.first_rev, status.last_rev)
                        diff_lines += expanded_diff
                
                except Exception, e:
                    raise ChangeSetError('Problem with ' + path +': '+ str(e))
        
            os.rmdir(temp_dir_name)
            
        except Exception, e:
            raise ChangeSetError('Error creating diff: ' + str(e) )
        
        if len(diff_lines) < 3:
            raise ChangeSetError('There is no source code difference. The changesets might contain binary files and folders only or neutralize themselves.')
        
        return DiffFile(''.join(diff_lines), description)
    
    
    def _expand_filename(self, diff, fullname, rev1, rev2):
        difflines = diff.splitlines(True)
        
        if len(difflines) < 4:
            return difflines
        else:
            difflines[0] = unicode('Index: ' + fullname + '\n')
            difflines[2] = unicode('--- ' + fullname + '\t(revision ' + str(rev1) + ')\n')
            difflines[3] = unicode('+++ ' + fullname + '\t(revision ' + str(rev2) + ')\n')            
            return difflines
            
            
            
                
    def _get_diff_of_new_file(self, filename, new_revision):
        # is same like diff with empty content
        rev = Revision(opt_revision_kind.number, new_revision)
        content = self.tool.client.cat(filename, rev)
        diff_lines = content.splitlines(True)

        file_len = len(diff_lines)

        if not diff_lines[file_len - 1].endswith(u'\n'):
            diff_lines.append(u'\n\\ No newline at end of content\n')

        for idx in range(0, file_len):
            diff_lines[idx] = u'+' + diff_lines[idx]
            
        diff_lines.insert(0, u'@@ -0,0 +1,%d @@\n' % file_len)     # @@ -R +R @@ with R = l,s with l=line and s=block size in number of lines
        diff_lines.insert(0, u'+++ %s\t%s;%s\n' % (filename, filename, str(new_revision)))
        diff_lines.insert(0, u'--- %s\t%s;0\n' % (filename, filename))
        
        return diff_lines
        
        
    def _get_diff_of_deleted_file(self, filename, last_revision):
        # is same like diff with empty file
        rev = Revision(opt_revision_kind.number, last_revision)
        content = self.tool.client.cat(filename, rev)
        diff_lines = content.splitlines(True)

        file_len = len(diff_lines)
       
        if not diff_lines[file_len - 1].endswith(u'\n'):
            diff_lines.append(u'\n\\ No newline at end of file\n')

        for idx in range(0, file_len):
            diff_lines[idx] = u'-' + diff_lines[idx]

        diff_lines.insert(0, u'+' + self.tool.DELETED_FILE + '\n')
            
        diff_lines.insert(0, u'@@ -1,%d +1,1 @@\n' % file_len)     # @@ -R +R @@ with R = l,s with l=line and s=block size in number of lines
        diff_lines.insert(0, u'+++ %s\t%s;0\n' % (filename, filename))
        diff_lines.insert(0, u'--- %s\t%s;%s\n' % (filename, filename, str(last_revision)))
        
        return diff_lines   
        
    def get_revision_info(self, revision):
        # TODO is this function really needed????
        rev = Revision(opt_revision_kind.number, revision)
        logs = self.tool.client.log(self.tool.repopath, rev, rev, True)
        
        if len(logs) != 1:
            raise ChangeSetError('Revision ' + str(revision) +' not found')
        
        revision = {'revision': revision, 
                    'user': logs[0].author or '', 
                    'description':logs[0].message or '', 
                    'changes':logs[0].changed_paths, 
                    'date':logs[0].date}
        return revision


    def merge_revision_into_list_of_modified_files(self, revision, modified_files):

        revInfo = self.get_revision_info(revision)
            
        if len(revInfo['changes']) == 0:
            raise EmptyChangeSetError(revision)

        for change in revInfo['changes']:
            action = change['action']
            path = change['path']
            
            if modified_files.has_key(path):
                modified_files[path].update(revision, action)
            else:
                modified_files[path] = DiffStatus(revision, action) 
  
        submit_date = datetime.datetime.fromtimestamp(revInfo['date'])        
        time_str = submit_date.strftime("%Y-%m-%d %I:%M %p")

        description =  'Change ' + str(revision) + ' by ' + revInfo['user'] + ' on ' + time_str +'\n'
        desclines = revInfo['description'].splitlines(True)
        for line in desclines:
            description += '       ' + line
        description += '\n\n\n'
        
        return description
    
    

