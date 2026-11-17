/* SPDX-License-Identifier: MIT
 * A pipe-to-PTY bridge for the official feeder's interactive wizard and unbuffered service output.
 * The child runs with a controlling terminal. The bridge keeps a Windows job
 * handle so a killed bridge cannot leave its child running.
 */
#include <windows.h>
#include <sys/cygwin.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <pty.h>
#include <termios.h>
#include <poll.h>
#include <signal.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static volatile sig_atomic_t stop_requested;
static void request_stop(int sig) { (void)sig; stop_requested = 1; }
static int copy_all(int fd, const char *data, size_t size) {
  while (size) { ssize_t n = write(fd, data, size); if (n < 0 && errno == EINTR) continue;
    if (n <= 0) return -1;
    data += n;
    size -= n;
  }
  return 0;
}
int main(int argc, char **argv) {
  int relay_input = 1, first_program = 1;
  if (argc > 1 && !strcmp(argv[1], "--no-input")) { relay_input = 0; first_program = 2; }
  if (argc <= first_program) { fputs("Usage: frw-pty [--no-input] PROGRAM [ARGUMENTS...]\n", stderr); return 2; }
  int master = -1, ready[2], status = 0; pid_t child;
  if (pipe(ready) < 0) { perror("readiness pipe"); return 1; }
  child = forkpty(&master, NULL, NULL, NULL);
  if (child < 0) { perror("forkpty"); return 1; }
  if (!child) {
    char go; close(ready[1]);
    if (read(ready[0], &go, 1) != 1) _exit(125);
    close(ready[0]);
    execvp(argv[first_program], &argv[first_program]); perror("exec"); _exit(127);
  }
  close(ready[0]);
  HANDLE job = CreateJobObjectW(NULL, NULL);
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = {0};
  limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
  DWORD child_winpid = (DWORD)cygwin_internal(CW_CYGWIN_PID_TO_WINPID, child);
  HANDLE child_handle = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, FALSE, child_winpid);
  if (!job || !child_handle || !SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits)) || !AssignProcessToJobObject(job, child_handle)) {
    fprintf(stderr, "Could not supervise terminal child (Windows error %lu).\n", (unsigned long)GetLastError());
    close(ready[1]); kill(child, SIGKILL); waitpid(child, &status, 0);
    if (child_handle) CloseHandle(child_handle);
    if (job) CloseHandle(job);
    close(master);
    return 1;
  }
  CloseHandle(child_handle);
  struct termios term;
  if (tcgetattr(master, &term) == 0) { term.c_lflag &= ~(ECHO | ECHONL); tcsetattr(master, TCSANOW, &term); }
  signal(SIGTERM, request_stop); signal(SIGINT, request_stop); signal(SIGHUP, request_stop); signal(SIGPIPE, SIG_IGN);
  if (write(ready[1], "G", 1) != 1) stop_requested = 1;
  close(ready[1]);
  char buf[4096];
  while (!stop_requested) {
    struct pollfd fds[2] = {{relay_input ? STDIN_FILENO : -1, POLLIN, 0}, {master, POLLIN, 0}};
    int rc = poll(fds, 2, 100);
    if (rc < 0) { if (errno == EINTR) continue; break; }
    if (fds[1].revents & POLLIN) {
      ssize_t n = read(master, buf, sizeof(buf)); if (n <= 0 || copy_all(STDOUT_FILENO, buf, n)) break;
    }
    if ((fds[1].revents & (POLLERR | POLLHUP | POLLNVAL)) && !(fds[1].revents & POLLIN)) break;
    if (fds[0].revents & (POLLIN | POLLHUP)) {
      ssize_t n = read(STDIN_FILENO, buf, sizeof(buf)); if (n <= 0 || copy_all(master, buf, n)) break;
    }
    if (fds[0].revents & (POLLERR | POLLNVAL)) break;
  }
  close(master);
  if (child > 0) {
    kill(-child, SIGTERM);
    for (int i = 0; i < 20; ++i) { if (waitpid(child, &status, WNOHANG) == child) { child = -1; break; } usleep(50000); }
    if (child > 0) { kill(-child, SIGKILL); waitpid(child, &status, 0); }
  }
  CloseHandle(job);
  return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
