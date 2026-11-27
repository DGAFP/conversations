"""Join the RH source authority and upstream streaming migration branches."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0016_assistantrhmessagerun"),
        ("chat", "0016_merge_20261105_2010"),
    ]

    operations = []
