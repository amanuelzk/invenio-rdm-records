from invenio_db import db
from marshmallow import Schema, fields, missing, pre_dump
from sqlalchemy.dialects.postgresql import JSON

class SavedRecords(db.Model):
    """File associated with a draft."""

    __tablename__ = "saved_records"
    user_id = db.Column(db.Integer,nullable=False)
    record_id = db.Column(db.Text,nullable=False,primary_key=True)
    # json = db.Column(JSON)
    def __init__(self, user_id, record_id):
        self.user_id = user_id
        self.record_id = record_id
        # self.json = json
class SaveRecordsSchema(Schema):
  class Meta:
    fields = ('user_id', 'record_id')
# save_record_schema = SaveRecordsSchema(strict=True)
save_records_schema = SaveRecordsSchema(many=True)
