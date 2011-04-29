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
    
    def get_diff_file(self, changelist_numbers):
        if changelist_numbers == None or len(changelist_numbers) == 0:
            raise SCMError('List of changelist numbers is empty')
        #diff_tool = PerforceDiffTool(self)
        return None #diff_tool.get_diff_file(changelist_numbers)

        

