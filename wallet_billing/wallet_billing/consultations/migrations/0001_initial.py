

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ConsultationSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('unassigned', 'Unassigned'), ('assigned', 'Assigned'), ('accepted_by_provider', 'Accepted'), ('in_progress', 'In Progress'), ('completed', 'Completed')], default='unassigned', max_length=30)),
                ('per_minute_rate', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('billed_minutes', models.PositiveIntegerField(default=0)),
                ('duration_seconds', models.PositiveIntegerField(blank=True, null=True)),
                ('ended_by', models.CharField(blank=True, choices=[('user', 'User'), ('provider', 'Provider'), ('system', 'System')], max_length=10)),
                ('end_reason', models.CharField(blank=True, choices=[('normal', 'Ended normally'), ('insufficient_balance', 'Insufficient Balance')], max_length=30)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('provider', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='provider_sessions', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='consultation_sessions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-id'],
                'constraints': [models.UniqueConstraint(condition=models.Q(('status', 'in_progress')), fields=('user',), name='one_in_progress_session_per_user'), models.CheckConstraint(condition=models.Q(('per_minute_rate__isnull', True), ('per_minute_rate__gt', 0), _connector='OR'), name='session_rate_positive'), models.CheckConstraint(condition=models.Q(models.Q(('status__in', ['in_progress', 'completed']), _negated=True), models.Q(('started_at__isnull', False), ('per_minute_rate__isnull', False)), _connector='OR'), name='started_session_has_start_time_and_rate'), models.CheckConstraint(condition=models.Q(models.Q(('status', 'completed'), _negated=True), models.Q(('ended_at__isnull', False), models.Q(('ended_by', ''), _negated=True)), _connector='OR'), name='completed_session_has_end_info')],
            },
        ),
    ]
