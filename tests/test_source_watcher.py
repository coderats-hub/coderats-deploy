import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "source_watcher", ROOT / "scripts" / "watch-source-events.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

A = "a" * 40
B = "b" * 40
C = "c" * 40

class SourceWatcherTests(unittest.TestCase):
    def test_dispatch_routes(self):
        self.assertEqual(watch.route_event("web","develop"),"deploy-private-dev.yml")
        self.assertEqual(watch.route_event("api","main"),"validate-private-source.yml")
        self.assertEqual(watch.route_event("grader","develop"),"deploy-grader-dev.yml")

    def test_reject_invalid_branch(self):
        with self.assertRaises(ValueError):
            watch.route_event("web","feature/untrusted")

    def test_compare_fast_forward(self):
        compare={"status":"ahead","ahead_by":2,"commits":[{"sha":B},{"sha":C}]}
        self.assertEqual(watch.changed_commits(A,C,compare),[B,C])

    def test_reject_force_push_and_incomplete_history(self):
        with self.assertRaises(ValueError):
            watch.changed_commits(A,C,{"status":"diverged","ahead_by":1,"commits":[{"sha":C}]})
        with self.assertRaises(ValueError):
            watch.changed_commits(A,C,{"status":"ahead","ahead_by":5,"commits":[{"sha":C}]})

    def test_initial_state_and_no_auto_dispatch(self):
        class FakeAPI:
            def __init__(self):
                self.rows={}
                self.queued=[]
            def call(self,path,private=False,allow_not_found=False):
                if "/branches/develop" in path or "/branches/main" in path:
                    return {"commit":{"sha":A}}
                return None
            def state(self,key):
                return self.rows.get(key)
            def save(self,key,sha):
                self.rows[key]=sha
            def dispatch(self,*args):
                self.queued.append(args)
        api=FakeAPI()
        watch.scan_branches(api,enabled=True)
        self.assertEqual(len(api.rows),len(watch.SERVICES)*len(watch.BRANCHES))
        self.assertEqual(api.queued,[])

if __name__ == "__main__":
    unittest.main()
