from __future__ import unicode_literals, division, absolute_import
from tests import FlexGetBase, with_filecopy


class TestTorrentSize(object):

    config = """
        tasks:
          test_min:
            mock:
              - {title: 'test', file: 'test_min.torrent'}
            accept_all: yes
            content_size:
              min: 2000

          test_max:
            mock:
              - {title: 'test', file: 'test_max.torrent'}
            accept_all: yes
            content_size:
              max: 10

          test_strict:
            mock:
              - {title: 'test'}
            accept_all: yes
            content_size:
              min: 1
              strict: yes

          test_cache:
            mock:
              - {title: 'test', url: 'http://localhost/', file: 'test.torrent'}
            accept_all: yes
            content_size:
              min: 2000
    """

    @with_filecopy('test.torrent', 'test_min.torrent')
    def test_min(self, execute_task):
        """Content Size: torrent with min size"""
        task = execute_task('test_min')
        assert task.find_entry('rejected', title='test'), \
            'should have rejected, minimum size'

    @with_filecopy('test.torrent', 'test_max.torrent')
    def test_max(self, execute_task):
        """Content Size: torrent with max size"""
        task = execute_task('test_max')
        assert task.find_entry('rejected', title='test'), \
            'should have rejected, maximum size'

    @with_filecopy('test.torrent', 'test_strict.torrent')
    def test_strict(self, execute_task):
        """Content Size: strict enabled"""
        task = execute_task('test_strict')
        assert task.find_entry('rejected', title='test'), \
            'should have rejected non torrent'

    def test_cache(self, execute_task):
        """Content Size: caching"""
        task = execute_task('test_cache')
        assert task.find_entry('rejected', title='test'), \
            'should have rejected, too small'

        # Make sure remember_rejected rejects on the second execution
        task = execute_task('test_cache')
        assert task.find_entry('rejected', title='test', rejected_by='remember_rejected'), \
            'should have rejected, size present from the cache'


class TestFileSize(object):
    """This is to test that content_size is picked up from the file itself when filesystem is used as the input.
    This doesn't do a super job of testing, because we don't have any test files bigger than 1 MB."""

    config = """
        tasks:
          test_min:
            mock:
              - {title: 'test', location: 'min.file'}
            accept_all: yes
            content_size:
              min: 2000

          test_max:
            mock:
              - {title: 'test', location: 'max.file'}
            accept_all: yes
            content_size:
              max: 2000

          test_torrent:
            mock:
              # content_size should not be read for this directly, as it is a torrent file
              - {title: 'test', location: 'test.torrent'}
    """

    @with_filecopy('test.torrent', 'min.file')
    def test_min(self, execute_task):
        """Content Size: torrent with min size"""
        task = execute_task('test_min')
        entry = task.find_entry('rejected', title='test')
        assert entry, 'should have rejected, minimum size'
        assert entry['content_size'] == 0, \
            'content_size was not detected'

    @with_filecopy('test.torrent', 'max.file')
    def test_max(self, execute_task):
        """Content Size: torrent with max size"""
        task = execute_task('test_max')
        entry = task.find_entry('accepted', title='test')
        assert entry, 'should have been accepted, it is below maximum size'
        assert entry['content_size'] == 0, \
            'content_size was not detected'

    def test_torrent(self, execute_task):
        task = execute_task('test_torrent')
        entry = task.find_entry('entries', title='test')
        assert 'content_size' not in entry, \
            'size of .torrent file should not be read as content_size'
