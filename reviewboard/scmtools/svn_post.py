import re
import subprocess
import os
import sys
import time
import datetime


from tempfile import mkstemp, mkdtemp

from reviewboard.scmtools.svn import SVNTool
from reviewboard.scmtools.errors import SCMError, EmptyChangeSetError, ChangeSetError



class SVNPostCommitTool(SVNTool):
    support_post_commit = True

    EMPTY_FILE = '<FILE IS EMPTY>'
    REMOVED_FILE = '<FILE WAS REMOVED>'

    
    def __init__(self, repository):
        SVNTool.__init__(self, repository)
    
    def get_diff_file(self, changelist_numbers):
        if changelist_numbers == None or len(changelist_numbers) == 0:
            raise ChangeSetError('No change list numbers')
        #diff_tool = PerforceDiffTool(self)
        #return diff_tool.get_diff_file(changelist_numbers)
        return None # TODO
    
    def get_fields(self):
        return ['change_numbers']
