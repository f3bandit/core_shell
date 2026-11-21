"""cmdpipe: the check (cmdpipe_check.py, a script: a fake Main and FIFOs in a temp folder) run as a test."""
import os, subprocess, sys, unittest


class CmdPipe(unittest.TestCase):
    def test_check(self):
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cmdpipe_check.py')],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout.decode(errors='replace')[-2000:])

    def test_forward_full_pipe(self):
        """Main's FIFO full (EAGAIN on write): logged and False, never raised into the pipe's thread."""
        import tempfile
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from coreshell import cmdpipe
        d = tempfile.mkdtemp()
        main = os.path.join(d, 'MiSTer_cmd_main')
        os.mkfifo(main)
        rfd = os.open(main, os.O_RDWR | os.O_NONBLOCK)      # a reader that never reads
        logs = []
        p = cmdpipe.CmdPipe(lambda l: None, cmd=os.path.join(d, 'MiSTer_cmd'), main=main, log=logs.append,
                            mark=os.path.join(d, 'mark'))
        p.ours = 1                                          # (Main reads its second name)
        try:
            ok = True
            for _ in range(20000):                          # fill the pipe
                ok = p.forward('volume up ' + 'x' * 100)
                if not ok:
                    break
            self.assertFalse(ok)
            self.assertTrue(any('not passed' in l for l in logs))
        finally:
            os.close(rfd)

    def test_take_waits_for_main_to_open(self):
        """Main makes its FIFO, then opens it: not renamed before Main has it open (Main would open Coreshell's by
        the name, and its own would have no reader)."""
        import tempfile, time
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from coreshell import cmdpipe
        d = tempfile.mkdtemp()
        cmd = os.path.join(d, 'MiSTer_cmd')
        os.mkfifo(cmd)                                      # made, not opened yet
        p = cmdpipe.CmdPipe(lambda l: None, cmd=cmd, main=os.path.join(d, 'MiSTer_cmd_main'),
                            mark=os.path.join(d, 'mark'))
        self.assertFalse(p._take())
        self.assertFalse(os.path.exists(p.main))
        fd = os.open(cmd, os.O_RDWR | os.O_NONBLOCK)        # Main opens it (as input.cpp does)
        try:
            self.assertTrue(p._take())
            self.assertTrue(os.path.exists(p.main))
            cmdpipe.write_main(p.main, 'load_core x.mgl')
            self.assertEqual(os.read(fd, 100), b'load_core x.mgl\n')
        finally:
            os.close(fd)
            p._give_back()

    def test_write_main_no_reader(self):
        """A FIFO nobody reads: an error after the timeout, never a write that waits for good."""
        import tempfile, time
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from coreshell import cmdpipe
        f = os.path.join(tempfile.mkdtemp(), 'MiSTer_cmd_main')
        os.mkfifo(f)
        t = time.time()
        with self.assertRaises(OSError):
            cmdpipe.write_main(f, 'load_core x.mgl', timeout=0.3)
        self.assertLess(time.time() - t, 2)


if __name__ == '__main__':
    unittest.main()
