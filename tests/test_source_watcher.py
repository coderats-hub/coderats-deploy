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
        self.assertEqual(watch.route_event("web","develop"),"validate-private-source.yml")
        self.assertEqual(watch.route_event("api","main"),"validate-private-source.yml")
        self.assertEqual(watch.route_event("grader","develop"),"validate-private-source.yml")

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

    def test_initial_state_baselines_without_retroactive_ci(self):
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
        watch.scan_branches(api)
        self.assertEqual(len(api.rows),len(watch.SERVICES)*len(watch.BRANCHES))
        self.assertEqual(api.queued,[])

    def test_all_commits_only_trigger_central_ci(self):
        class FakeAPI:
            def __init__(self):
                self.rows={watch.variable_name(s,b):A for s in watch.SERVICES for b in watch.BRANCHES}
                self.queued=[]
            def call(self,path,private=False,allow_not_found=False):
                if "/branches/" in path:
                    return {"commit":{"sha":C}}
                if "/compare/" in path:
                    return {"status":"ahead","ahead_by":2,"commits":[{"sha":B},{"sha":C}]}
                return None
            def state(self,key):
                return self.rows.get(key)
            def save(self,key,sha):
                self.rows[key]=sha
            def dispatch(self,workflow,service,sha,trigger="develop",pr_number=0):
                self.queued.append((workflow,service,sha,trigger))
        api=FakeAPI()
        watch.scan_branches(api)
        self.assertEqual(len(api.queued),len(watch.SERVICES)*len(watch.BRANCHES)*2)
        self.assertTrue(all(w=="validate-private-source.yml" for w,_,_,_ in api.queued))

if __name__ == "__main__":
    unittest.main()
