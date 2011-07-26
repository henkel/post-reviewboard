from django import forms
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.utils.translation import ugettext as _
from reviewboard.diffviewer import forms as diffviewer_forms
from reviewboard.diffviewer.models import DiffSet
from reviewboard.reviews.errors import OwnershipError, RevisionTableUpdated
from reviewboard.reviews.models import DefaultReviewer, ReviewRequest, \
    ReviewRequestDraft, Screenshot
from reviewboard.scmtools.errors import SCMError, ChangeNumberInUseError, \
    InvalidChangeNumberError, ChangeSetError
from reviewboard.scmtools.models import Repository
import logging
import re




class DefaultReviewerForm(forms.ModelForm):
    name = forms.CharField(
        label=_("Name"),
        max_length=64,
        widget=forms.TextInput(attrs={'size': '30'}))

    file_regex = forms.CharField(
        label=_("File regular expression"),
        max_length=256,
        widget=forms.TextInput(attrs={'size': '60'}),
        help_text=_('File paths are matched against this regular expression '
                    'to determine if these reviewers should be added.'))

    repository = forms.ModelMultipleChoiceField(
        label=_('Repositories'),
        required=False,
        queryset=Repository.objects.filter(visible=True).order_by('name'),
        help_text=_('The list of repositories to specifically match this '
                    'default reviewer for. If left empty, this will match '
                    'all repositories.'),
        widget=FilteredSelectMultiple(_("Repositories"), False))

    def clean_file_regex(self):
        """Validates that the specified regular expression is valid."""
        file_regex = self.cleaned_data['file_regex']

        try:
            re.compile(file_regex)
        except Exception, e:
            raise forms.ValidationError(e)

        return file_regex

    class Meta:
        model = DefaultReviewer


class NewReviewRequestForm(forms.Form):
    """
    A form that handles creationg of new review requests. These take
    information on the diffs, the repository the diffs are against, and
    optionally a changelist number (for use in certain repository types
    such as Perforce).
    """
    NO_REPOSITORY_ENTRY = _('(None - Graphics only)')

    basedir = forms.CharField(
        label=_("Base Directory"),
        required=False,
        help_text=_("The absolute path in the repository the diff was "
                    "generated in."),
        widget=forms.TextInput(attrs={'size': '35'}))
    diff_path = forms.FileField(
        label=_("Diff"),
        required=False,
        help_text=_("The new diff to upload."),
        widget=forms.FileInput(attrs={'size': '35'}))
    parent_diff_path = forms.FileField(
        label=_("Parent Diff"),
        required=False,
        help_text=_("An optional diff that the main diff is based on. "
                    "This is usually used for distributed revision control "
                    "systems (Git, Mercurial, etc.)."),
        widget=forms.FileInput(attrs={'size': '35'}))
    repository = forms.ModelChoiceField(
        label=_("Repository"),
        queryset=Repository.objects.filter(visible=True).order_by('name'),
        empty_label=NO_REPOSITORY_ENTRY,
        required=False)

    changenum = forms.IntegerField(label=_("Change Number"), required=False)

    field_mapping = {}

    def __init__(self, *args, **kwargs):
        forms.Form.__init__(self, *args, **kwargs)

        # Repository ID : visible fields mapping.  This is so we can
        # dynamically show/hide the relevant fields with javascript.
        valid_repos = [('', self.NO_REPOSITORY_ENTRY)]

        repo_ids = [
            id for (id, _) in self.fields['repository'].choices if id
        ]

        # Show the explanation for the "None" entry when it's selected.
        self.field_mapping[''] = ['no_repository_explanation']

        for repo in Repository.objects.filter(pk__in=repo_ids).order_by("name"):
            try:
                self.field_mapping[repo.id] = repo.get_scmtool().get_fields()
                valid_repos.append((repo.id, repo.name))
            except Exception, e:
                logging.error('Error loading SCMTool for repository '
                              '%s (ID %d): %s' % (repo.name, repo.id, e),
                              exc_info=1)

        self.fields['repository'].choices = valid_repos

        # If we have any repository entries we can show, then we should
        # show the first one, rather than the "None" entry.
        if len(valid_repos) > 1:
            self.fields['repository'].initial = valid_repos[1][0]


    @staticmethod
    def create_from_list(data, constructor, error):
        """Helper function to combine the common bits of clean_target_people
           and clean_target_groups"""
        names = [x for x in map(str.strip, re.split(',\s*', data)) if x]
        return set([constructor(name) for name in names])


    def create(self, user, diff_file, parent_diff_file):
        repository = self.cleaned_data['repository']
        changenum = self.cleaned_data['changenum'] or None

        # It's a little odd to validate this here, but we want to have access to
        # the user.
        if changenum:
            try:
                changeset = repository.get_scmtool().get_changeset(changenum)
            except NotImplementedError:
                # This scmtool doesn't have changesets
                pass
            except SCMError, e:
                self.errors['changenum'] = forms.util.ErrorList([str(e)])
                raise ChangeSetError()
            except ChangeSetError, e:
                self.errors['changenum'] = forms.util.ErrorList([str(e)])
                raise e

            if not changeset:
                self.errors['changenum'] = forms.util.ErrorList([
                    'This change number does not represent a valid '
                    'changeset.'])
                raise InvalidChangeNumberError()

            if user.username != changeset.username:
                self.errors['changenum'] = forms.util.ErrorList([
                    'This change number is owned by another user.'])
                raise OwnershipError()

        try:
            review_request = ReviewRequest.objects.create(user, repository,
                                                          changenum)
        except ChangeNumberInUseError:
            # The user is updating an existing review request, rather than
            # creating a new one.
            review_request = ReviewRequest.objects.get(changenum=changenum,
                                                       repository=repository)
            review_request.update_from_changenum(changenum)

            if review_request.status == 'D':
                # Act like we're creating a brand new review request if the
                # old one is discarded.
                review_request.status = 'P'
                review_request.public = False

            review_request.save()

        if diff_file:
            diff_form = UploadDiffForm(
                review_request,
                data={
                    'basedir': self.cleaned_data['basedir'],
                },
                files={
                    'path': diff_file,
                    'parent_diff_path': parent_diff_file,
                })
            diff_form.full_clean()

            class SavedError(Exception):
                """Empty exception class for when we already saved the
                error info.
                """
                pass

            try:
                diff_form.create(diff_file, parent_diff_file,
                                 attach_to_history=True)
                if 'path' in diff_form.errors:
                    self.errors['diff_path'] = diff_form.errors['path']
                    raise SavedError
                elif 'base_diff_path' in diff_form.errors:
                    self.errors['base_diff_path'] = diff_form.errors['base_diff_path']
                    raise SavedError
            except SavedError:
                review_request.delete()
                raise
            except diffviewer_forms.EmptyDiffError:
                review_request.delete()
                self.errors['diff_path'] = forms.util.ErrorList([
                    'The selected file does not appear to be a diff.'])
                raise
            except Exception, e:
                review_request.delete()
                self.errors['diff_path'] = forms.util.ErrorList([e])
                raise

        review_request.add_default_reviewers()
        review_request.save()
        return review_request


class UploadDiffForm(diffviewer_forms.UploadDiffForm):
    """
    A specialized UploadDiffForm that knows how to interact with review
    requests.
    """
    def __init__(self, review_request, data=None, *args, **kwargs):
        super(UploadDiffForm, self).__init__(review_request.repository,
                                             data, *args, **kwargs)
        self.review_request = review_request

        if ('basedir' in self.fields and
            (not data or 'basedir' not in data)):
            try:
                diffset = review_request.diffset_history.diffsets.latest()
                self.fields['basedir'].initial = diffset.basedir
            except DiffSet.DoesNotExist:
                pass

    def create(self, diff_file, parent_diff_file=None,
               attach_to_history=False):
        history = None

        if attach_to_history:
            history = self.review_request.diffset_history

        diffset, description = super(UploadDiffForm, self).create(diff_file,
                                                     parent_diff_file,
                                                     history)

        if not attach_to_history:
            # Set the initial revision to be one newer than the most recent
            # public revision, so we can reference it in the diff viewer.
            #
            # TODO: It would be nice to later consolidate this with the logic
            #       in DiffSet.save.
            public_diffsets = self.review_request.diffset_history.diffsets

            try:
                latest_diffset = public_diffsets.latest()
                diffset.revision = latest_diffset.revision + 1
            except DiffSet.DoesNotExist:
                diffset.revision = 1

            diffset.save()

        return diffset, description


class UploadScreenshotForm(forms.Form):
    """
    A form that handles uploading of new screenshots.
    A screenshot takes a path argument and optionally a caption.
    """
    caption = forms.CharField(required=False)
    path = forms.ImageField(required=True)

    def create(self, file, review_request):
        screenshot = Screenshot(caption=self.cleaned_data['caption'],
                                draft_caption=self.cleaned_data['caption'])
        screenshot.image.save(file.name, file, save=True)

        review_request.screenshots.add(screenshot)

        draft = ReviewRequestDraft.create(review_request)
        draft.screenshots.add(screenshot)
        draft.save()

        return screenshot


class MultiChoiceWithMeaningfulIdsButNoValidation(forms.MultipleChoiceField):
    def validate(self, value):
        # Choices are created dynamically and cannot be validated
        pass
        
    # Sorry for copy&pasting this from django/trunk/django/forms/widgets.py, 
    # but I really needed to change the html element id of the checkboxes
    # and did not find a better way...
    # The render function below is licenced under the following terms:
    #Copyright (c) Django Software Foundation and individual contributors.
    #All rights reserved.

    #Redistribution and use in source and binary forms, with or without modification,
    #are permitted provided that the following conditions are met:

        #1. Redistributions of source code must retain the above copyright notice, 
           #this list of conditions and the following disclaimer.
        
        #2. Redistributions in binary form must reproduce the above copyright 
           #notice, this list of conditions and the following disclaimer in the
           #documentation and/or other materials provided with the distribution.

        #3. Neither the name of Django nor the names of its contributors may be used
           #to endorse or promote products derived from this software without
           #specific prior written permission.

    #THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
    #ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
    #WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
    #DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
    #ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
    #(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
    #LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
    #ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
    #(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
    #SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
    def render(self, name, value, attrs=None, choices=()):
        if value is None: value = []
        has_id = attrs and 'id' in attrs
        final_attrs = self.build_attrs(attrs, name=name)
        output = [u'<ul>']
        # Normalize to strings
        str_values = set([force_unicode(v) for v in value])
        for i, (option_value, option_label) in enumerate(chain(self.choices, choices)):
            # If an ID attribute was given, add a numeric index as a suffix,
            # so that the checkboxes don't all have the same ID attribute.
            if has_id:
                final_attrs = dict(final_attrs, id='%s_%s' % (attrs['id'], option_value))
                label_for = u' for="%s"' % final_attrs['id']
            else:
                label_for = ''

            cb = CheckboxInput(final_attrs, check_test=lambda value: value in str_values)
            option_value = force_unicode(option_value)
            rendered_cb = cb.render(name, option_value)
            option_label = conditional_escape(force_unicode(option_label))
            output.append(u'<li><label%s>%s %s</label></li>' % (label_for, rendered_cb, option_label))
        output.append(u'</ul>')
        return mark_safe(u'\n'.join(output))


class NewPostReviewRequestForm(forms.Form):
    """
    A form that handles creationg of new review requests. These take
    information on the diffs, the repository the diffs are against, and
    optionally a changelist number (for use in certain repository types
    such as Perforce).
    """
    NO_REPOSITORY_ENTRY = _('(None - Graphics only)')

    repository = forms.ModelChoiceField(
        label=_("Repository"),
        queryset=Repository.objects.filter(visible=True).order_by('name'),
        empty_label=NO_REPOSITORY_ENTRY,
        required=False)

    # match ' 23 42 3343 ' or ' 23, 34 , 235235 '
    revisions = forms.RegexField(regex=r'^(\s*[A-F,a-f,0-9]+\s*,{0,1}\s*)+$',
                                 label=_('List of Revisions'),
                                 max_length=2048,
                                 required=False,
                                 widget=forms.TextInput(attrs={'size':'50'}),
                                 help_text=_('A list of revision identifiers, e.g. 11235 57789 34567'))

    revisions_choice = MultiChoiceWithMeaningfulIdsButNoValidation(required=False, widget=forms.CheckboxSelectMultiple)

    LOAD_REVISIONS_BUTTON__SHOW = _('Get Revisions')
    LOAD_REVISIONS_BUTTON__UPDATE = _('Refresh Revisions')
    load_revisions_button = LOAD_REVISIONS_BUTTON__SHOW

    ignore_revisions_button = _('Ignore selected')
    showall_revisions_button = _('Include all')

    REVISIONS_CHOICE_HELP__SHOW = _('Click show to get a list of revisions which are not yet added to Review Board.')
    REVISIONS_CHOICE_HELP__UPDATE  = _('Add one or more revisions from the list below to your review request.')
    revisions_choice_help = REVISIONS_CHOICE_HELP__SHOW

    field_mapping = {}


    def __init__(self, *args, **kwargs):
        forms.Form.__init__(self, *args, **kwargs)

        # Repository ID : visible fields mapping.  This is so we can
        # dynamically show/hide the relevant fields with javascript.
        valid_repos = [('', self.NO_REPOSITORY_ENTRY)]

        repo_ids = [
            id for (id, _) in self.fields['repository'].choices if id
        ]

        # Show the explanation for the "None" entry when it's selected.
        self.field_mapping[''] = ['no_repository_explanation']

        for repo in Repository.objects.filter(pk__in=repo_ids).order_by("name"):
            try:
                tool = repo.get_scmtool()
                if hasattr(tool, "support_post_commit") and tool.support_post_commit:
                    self.field_mapping[repo.id] = tool.get_fields()
                    valid_repos.append((repo.id, repo.name))

            except Exception, e:
                logging.error('Error loading SCMTool for repository '
                              '%s (ID %d): %s' % (repo.name, repo.id, e),
                              exc_info=1)

        self.fields['repository'].choices = valid_repos

        # If we have any repository entries we can show, then we should
        # show the first one, rather than the "None" entry.
        if len(valid_repos) > 1:
            self.fields['repository'].initial = valid_repos[1][0]


    def create(self, user, diff_file):
        tool = None
        revisions_error_field = ''

        repository = self.cleaned_data['repository']

        if repository != None:
            tool = repository.get_scmtool();
            tool_fields = tool.get_fields()
            if 'revisions' in tool_fields:
                revisions_error_field = 'revisions'
            if 'revisions_choice' in tool_fields:
                revisions_error_field = 'revisions_choice'

        any_revisions_choice_button_clicked = 'load_revisions_button' in self.data or 'ignore_revisions_button' in self.data or 'showall_revisions_button' in self.data

        if any_revisions_choice_button_clicked:
            if not 'revisions_choice' in tool_fields:
                self.errors[revisions_error_field] = forms.util.ErrorList("Revision tracking is not supported by selected repository")
                raise RevisionTableUpdated()

        if 'showall_revisions_button' in self.data:
            # User clicked on showall_revisions_button
            tool.ignore_revisions(user.username, None)

        if 'ignore_revisions_button' in self.data:
            # User clicked on ignore_revisions_button
            if 'revisions_choice' in self.cleaned_data:
                revisions_to_be_ignored = []
                for rev in self.cleaned_data['revisions_choice']:
                    revisions_to_be_ignored.append(rev)
                tool.ignore_revisions(user.username, revisions_to_be_ignored)

        if any_revisions_choice_button_clicked:
            try:
                missing_revisions = tool.get_missing_revisions(user.username)
            except Exception, e:
                self.errors[revisions_error_field] = forms.util.ErrorList([str(e)])
                raise e

            if len(missing_revisions) == 0:
                self.fields['revisions_choice'].choices = []
                self.cleaned_data['revisions_choice'] = []
                self.errors[revisions_error_field] = forms.util.ErrorList("No pending revisions found.")
            else:
                missing_revisions.reverse()
                self.fields['revisions_choice'].choices = [ (rev[0], rev[0]+' '+rev[1]) for rev in missing_revisions ]
                self.load_revisions_button = self.LOAD_REVISIONS_BUTTON__UPDATE
                self.revisions_choice_help = self.REVISIONS_CHOICE_HELP__UPDATE

            raise RevisionTableUpdated()

        revision_list = []

        if 'revisions' in self.cleaned_data:
            split_field = re.split('\s*,{0,1}\s*', self.cleaned_data['revisions'])
            for rev in split_field:
                if rev.strip() != '':
                    revision_list.append(rev)

        if 'revisions_choice' in self.cleaned_data:
            for rev in self.cleaned_data['revisions_choice']:
                revision_list.append(rev)
                self.data['revisions'] += ' ' + str(rev)

        if len(revision_list) > 0:
            # Eliminate duplicates
            revision_list = list(set(revision_list))

        review_request = ReviewRequest.objects.create(user, repository)

        if diff_file == None and tool != None:
            try:
                # Create diff file
                diff_file = tool.get_diff_file(revision_list)

            except ChangeSetError, e:
                review_request.delete()
                self.errors[revisions_error_field] = forms.util.ErrorList("Could not create diff for specified revisions: " + str(e))
                raise
            except Exception, e:
                review_request.delete()
                self.errors[revisions_error_field] = forms.util.ErrorList([e])
                raise

            # Update summary and description
            review_request.summary = diff_file.name
            review_request.description = diff_file.description

        if diff_file:
            diff_form = UploadDiffForm(
                review_request,
                files={
                    'path': diff_file,
                })
            diff_form.full_clean()

            class SavedError(Exception):
                """Empty exception class for when we already saved the
                error info.
                """
                pass

            try:
                diff_form.create(diff_file, None, attach_to_history=True)
                if 'path' in diff_form.errors:
                    self.errors[revisions_error_field] = diff_form.errors['path']
                    raise SavedError
                elif 'base_diff_path' in diff_form.errors:
                    self.errors[revisions_error_field] = diff_form.errors['base_diff_path']
                    raise SavedError
            except SavedError:
                review_request.delete()
                raise OwnershipError()
            except diffviewer_forms.EmptyDiffError:
                review_request.delete()
                self.errors[revisions_error_field] = forms.util.ErrorList([
                    'The selected file does not appear to be a diff.'])
                raise
            except Exception, e:
                review_request.delete()
                self.errors[revisions_error_field] = forms.util.ErrorList([e])
                raise

        review_request.add_default_reviewers()
        review_request.save()
        return review_request
