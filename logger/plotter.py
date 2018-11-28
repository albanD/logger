import numpy as np
import pprint
import sys
import threading

# queue for python 2 / 3
if sys.version_info[0] == 2:
    from Queue import Queue
else:
    from queue import Queue

from collections import defaultdict

# optional visdom
try:
    import visdom
except ImportError:
    visdom = None


class Cache(object):
    def __init__(self):
        self.clear()

    def clear(self):
        self._x = []
        self._y = []

    def update(self, metric):
        self._x.append(metric.index.get())
        self._y.append(metric.get())

    @property
    def x(self):
        return np.array(self._x)

    @property
    def y(self):
        return np.array(self._y)

thread_status_marker = "marker"
def updateTraceWorker(viz, rcv_queue, send_queue):
    while True:
        opts = rcv_queue.get()
        if opts == thread_status_marker:
            send_queue.put(thread_status_marker)
            continue
        viz.line(**opts)
    print("bye")


class Plotter(object):

    def __init__(self, xp, visdom_opts, xlabel):
        super(Plotter, self).__init__()

        if visdom_opts is None:
            visdom_opts = {}

        assert visdom is not None, "visdom could not be imported"

        visdom_opts_keys = list(visdom_opts.keys())
        # Remove the unsafe_send opt if it is in visdom_opts
        self.unsafe_send = False
        if 'unsafe_send' in visdom_opts_keys:
            self.unsafe_send = bool(visdom_opts['unsafe_send'])
            del visdom_opts['unsafe_send']

        # visdom env is given by Experiment name unless specified
        if 'env' not in visdom_opts_keys:
            visdom_opts['env'] = xp.name

        self.viz = visdom.Visdom(**visdom_opts)
        self.xlabel = None if xlabel is None else str(xlabel)
        self.windows = {}
        self.windows_opts = defaultdict(dict)
        self.append = {}
        self.cache = defaultdict(Cache)
        self.append = True

        if self.unsafe_send:
            self.worker_queue = Queue()
            self.answer_queue = Queue()
            self.worker_thread = threading.Thread(target=updateTraceWorker,
                args=(self.viz, self.worker_queue, self.answer_queue))
            self.worker_thread.daemon = True
            self.worker_thread.start()

    def wait_sending(self):
        if self.unsafe_send:
            # Wait for the worker to process everything in the queue
            self.worker_queue.put(thread_status_marker)
            res = self.answer_queue.get()
            assert(res == thread_status_marker)

    def set_win_opts(self, name, opts):
        self.windows_opts[name] = opts

    def _plot_xy(self, name, tag, x, y, time_idx=True):
        """
        Creates a window if it does not exist yet.
        Returns True if data has been sent successfully, False otherwise.
        """
        tag = None if tag == 'default' else tag

        if name not in list(self.windows.keys()):
            opts = self.windows_opts[name]
            if 'xlabel' in opts:
                pass
            elif self.xlabel is not None:
                opts['xlabel'] = self.xlabel
            else:
                opts['xlabel'] = 'Time (s)' if time_idx else 'Index'

            if 'legend' not in opts and tag:
                opts['legend'] = [tag]
            if 'title' not in opts:
                opts['title'] = name
            self.windows[name] = self.viz.line(Y=y, X=x, opts=opts)
            return True
        else:
            if self.unsafe_send:
                args = {"Y": y, "X": x, "name": tag, "win": self.windows[name], "update": "append"}
                self.worker_queue.put(args)
                # Assume that the sending went right
                return True
            else:
                return bool(self.viz.line(Y=y, X=x, name=tag,
                                          win=self.windows[name],
                                          update="append"))

    def plot_xp(self, xp):

        if 'git_diff' in xp.config.keys():
            config = xp.config.copy()
            config.pop('git_diff')
        self.plot_config(config)

        for tag in sorted(xp.logged.keys()):
            for name in sorted(xp.logged[tag].keys()):
                self.plot_logged(xp.logged, tag, name)

    def plot_logged(self, logged, tag, name):
        xy = logged[tag][name]
        x = np.array(list(xy.keys())).astype(np.float)
        y = np.array(list(xy.values()))
        time_idx = not np.isclose(x, x.astype(np.int)).all()
        self._plot_xy(name, tag, x, y, time_idx)

    def plot_metric(self, metric):
        name, tag = metric.name, metric.tag
        cache = self.cache[metric.name_id()]
        cache.update(metric)
        sent = self._plot_xy(name, tag, cache.x, cache.y, metric.time_idx)
        # clear cache if data has been sent successfully
        if sent:
            cache.clear()

    def plot_config(self, config):
        config = dict((str(k), v) for (k, v) in config.items())
        # format dictionary with pretty print
        pp = pprint.PrettyPrinter(indent=4, width=1)
        msg = pp.pformat(config)
        # format with html
        msg = msg.replace('{', '')
        msg = msg.replace('}', '')
        msg = msg.replace('\n', '<br />')
        # display dict on visdom
        self.viz.text(msg)
