import unittest
from unittest.mock import patch, Mock
import subprocess
from builder.runtime import Deadline, DeadlineExceeded, run_bounded


class WallDeadlineTests(unittest.TestCase):
    def test_suspend_expires_budget_and_export_reserve(self):
        mono=[100.0];wall=[1000.0]
        d=Deadline(1800,clock=lambda:mono[0],wall_clock=lambda:wall[0])
        mono[0]+=10;wall[0]+=1501
        with self.assertRaises(DeadlineExceeded):d.timeout(120,reserve=300)
        self.assertEqual(d.remaining(),299)
        wall[0]+=300
        with self.assertRaises(DeadlineExceeded):d.require()

    def test_wall_rollback_cannot_restore_spent_budget(self):
        mono=[0.0];wall=[1000.0]
        d=Deadline(1800,clock=lambda:mono[0],wall_clock=lambda:wall[0])
        wall[0]+=1600;self.assertEqual(d.remaining(),200)
        wall[0]-=1600;mono[0]=1;self.assertEqual(d.remaining(),200)
        mono[0]=1800
        with self.assertRaises(DeadlineExceeded):d.require()

    def test_default_deadline_uses_both_clocks(self):
        with patch('builder.runtime.time.monotonic',side_effect=[10,11]),patch('builder.runtime.time.time',side_effect=[1000,2801]):
            d=Deadline()
            with self.assertRaises(DeadlineExceeded):d.require()

    def test_process_finishing_after_wall_limit_is_not_success(self):
        process=Mock(returncode=0);process.communicate.return_value=('late response','')
        with patch('builder.runtime.subprocess.Popen',return_value=process),patch('builder.runtime.time.monotonic',side_effect=[0,1]),patch('builder.runtime.time.time',side_effect=[100,551]):
            with self.assertRaises(subprocess.TimeoutExpired):run_bounded(['synthetic'],timeout=450)

    def test_suspend_timeout_terminates_process_group(self):
        process=Mock(pid=123);process.communicate.side_effect=[subprocess.TimeoutExpired('synthetic',30),('partial','')]
        with patch('builder.runtime.subprocess.Popen',return_value=process),patch('builder.runtime.time.monotonic',side_effect=[0,1]),patch('builder.runtime.time.time',side_effect=[100,551]),patch('builder.runtime.os.killpg') as kill:
            with self.assertRaises(subprocess.TimeoutExpired):run_bounded(['synthetic'],timeout=450,on_progress=Mock())
            kill.assert_called_once()
