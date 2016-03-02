from __future__ import unicode_literals, division, absolute_import

from tests import FlexGetBase, use_vcr

class TestHeaders(object):
    config = """
        tasks:
          test_headers:
            text:
              url: http://httpbin.org/cookies
              entry:
                title: '\"title\": \"(.*)\"'
                url: '\"url\": \"(.*)\"'
            headers:
              Cookie: "title=blah; url=other"
    """

    @use_vcr
    def test_headers(self, execute_task):
        task = execute_task('test_headers', options={'nocache': True})
        assert task.find_entry(title='blah', url='other'), 'Entry should have been created.'
