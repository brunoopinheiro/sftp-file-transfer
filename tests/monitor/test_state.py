from sftp_file_transfer.monitor.state import DashboardState, LogEntry


def test_log_entry_round_trips_through_dict():
    """Test LogEntry.to_dict/from_dict preserve all fields."""
    entry = LogEntry(ts='14:30:01', level='INFO', msg='Cycle #1 started')

    restored = LogEntry.from_dict(entry.to_dict())

    assert restored == entry


def test_dashboard_state_default_factory_produces_independent_instances():
    """Test that two default DashboardState instances don't share lists."""
    first = DashboardState()
    second = DashboardState()

    first.log_lines.append(LogEntry(ts='t', level='INFO', msg='m'))

    assert second.log_lines == []
    assert first.daily_counts is not second.daily_counts


def test_dashboard_state_round_trips_through_dict():
    """Test DashboardState.to_dict/from_dict preserve all fields."""
    state = DashboardState(
        site_name='GRANDHOTEL-SP01',
        cycle_num=42,
        last_cycle_time='14:30:08',
        last_cycle_status='SUCCESS',
        countdown_sec=30,
        db_connected=True,
        sftp_connected=False,
        sent_count=10,
        failed_count=2,
        pending_count=1,
        daily_counts=[1, 2, 3],
        log_lines=[LogEntry(ts='14:30:01', level='ERROR', msg='boom')],
    )

    restored = DashboardState.from_dict(state.to_dict())

    assert restored == state


def test_dashboard_state_round_trips_through_json():
    """Test DashboardState.to_json/from_json preserve all fields."""
    state = DashboardState(site_name='SITE-A', cycle_num=7)

    restored = DashboardState.from_json(state.to_json())

    assert restored == state


def test_append_log_line_bounds_the_ring_buffer():
    """Test append_log_line trims oldest entries beyond max_lines."""
    state = DashboardState()
    for i in range(5):
        state.append_log_line(
            LogEntry(ts=str(i), level='INFO', msg=f'line {i}'),
            max_lines=3,
        )

    assert [entry.msg for entry in state.log_lines] == [
        'line 2',
        'line 3',
        'line 4',
    ]
