"""Join the RH migration history with upstream NUL-safe JSON fields."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0017_merge_rh_sources_and_stream_chunks"),
        ("chat", "0016_nul_safe_json_fields"),
    ]

    operations = []
