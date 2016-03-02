from __future__ import unicode_literals, division, absolute_import
import sys
import tempfile

from nose.plugins.attrib import attr
from nose.plugins.skip import SkipTest
from nose.tools import assert_raises

from flexget.task import TaskAbort
from tests import FlexGetBase, use_vcr

# TODO more checks: fail_html, etc.
class TestDownload(object):
    config = """
        tasks:
          path_and_temp:
            mock:
              - {title: 'entry 1', url: 'http://www.speedtest.qsc.de/1kB.qsc'}
            accept_all: yes
            download:
              path: ~/
              temp: """ + tempfile.gettempdir() + """
          just_path:
            mock:
              - {title: 'entry 2', url: 'http://www.speedtest.qsc.de/10kB.qsc'}
            accept_all: yes
            download:
              path: ~/
          just_string:
            mock:
              - {title: 'entry 3', url: 'http://www.speedtest.qsc.de/100kB.qsc'}
            accept_all: yes
            download: ~/
      """

    @use_vcr
    def test_path_and_temp(self, execute_task):
        """Download plugin: Path and Temp directories set"""
        task = execute_task('path_and_temp')
        assert not task.aborted, 'Task should not have aborted'

    @use_vcr
    def test_just_path(self, execute_task):
        """Download plugin: Path directory set as dict"""
        task = execute_task('just_path')
        assert not task.aborted, 'Task should not have aborted'

    @use_vcr
    def test_just_string(self, execute_task):
        """Download plugin: Path directory set as string"""
        task = execute_task('just_string')
        assert not task.aborted, 'Task should not have aborted'


class TestDownloadTemp(object):
    config = """
        tasks:
          temp_wrong_permission:
            mock:
              - {title: 'entry 1', url: 'http://www.speedtest.qsc.de/1kB.qsc'}
            accept_all: yes
            download:
              path: ~/
              temp: /root
          temp_non_existent:
            download:
              path: ~/
              temp: /a/b/c/non/existent/
          temp_wrong_config_1:
            download:
              path: ~/
              temp: no
          temp_wrong_config_2:
            download:
              path: ~/
              temp: 3
          temp_empty:
            download:
              path: ~/
              temp:
        """
# TODO: These are really just config validation tests, and I have config validation turned off at the moment for unit
# tests due to some problems
'''
    def test_wrong_permission(self, execute_task):
        """Download plugin: Temp directory has wrong permissions"""
        if sys.platform.startswith('win'):
            raise SkipTest  # TODO: Windows doesn't have a guaranteed 'private' directory afaik
        task = execute_task('temp_wrong_permission', abort_ok=True)
        assert task.aborted

    def test_temp_non_existent(self, execute_task):
        """Download plugin: Temp directory does not exist"""
        task = execute_task('temp_non_existent', abort_ok=True)
        assert task.aborted

    def test_wrong_config_1(self, execute_task):
        """Download plugin: Temp directory config error [1of3]"""
        task = execute_task('temp_wrong_config_1', abort_ok=True)
        assert task.aborted

    def test_wrong_config_2(self, execute_task):
        """Download plugin: Temp directory config error [2of3]"""
        task = execute_task('temp_wrong_config_2', abort_ok=True)
        assert task.aborted

    def test_wrong_config_3(self, execute_task):
        """Download plugin: Temp directory config error [3of3]"""
        task = execute_task('temp_empty', abort_ok=True)
        assert task.aborted
'''
