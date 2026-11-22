from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload


class SizeLimitUploadHandler(FileUploadHandler):
    """Stops reading an upload past the limit, before it can fill the disk.

    Django parses a multipart body before a view checks who is asking, so this has to hold for
    every request. A stopped upload reaches the view with no file.
    """

    def __init__(self, request=None):
        super().__init__(request)
        self.received = 0

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > settings.MAX_ATTACHMENT_BYTES:
            raise StopUpload(connection_reset=False)
        return raw_data

    def file_complete(self, file_size):
        return None
