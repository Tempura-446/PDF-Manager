# Running the checks on your own PC

GitHub's own machines don't run jobs for this account, so the tests can run
on your Windows PC instead, as a "self-hosted runner". The workflows stay
the same. The runner only connects out to GitHub, picks up jobs for this
repository and runs them in its own folder. It costs no GitHub minutes.

What runs on the PC: the whole test suite (`tests.yml`, job "Windows PC /
tests"), in a fresh virtual environment each time, so your own Python and
its packages are left alone. What doesn't: the Linux jobs, and the
end-to-end job (`windows-e2e.yml`), because that one installs and then
uninstalls ZPDF in your own folders, which would remove your real copy.
Linux is checked where the changes are made, before each pull request.

## Before you start

- Python 3.10 or newer, with the `py` launcher (the python.org installer
  adds it).
- For the browser tests: Node.js with Playwright, as you already use for
  the Windows checks. Without them those tests are skipped, not failed.

## Set it up (once, about 10 minutes)

1. Make a folder with a short path outside OneDrive, for example
   `C:\actions-runner`.
2. On GitHub, open the repository's **Settings > Actions > Runners > New
   self-hosted runner**, choose **Windows** and **x64**.
3. Open PowerShell (not as administrator) in `C:\actions-runner` and run
   the **Download** commands from that page, then the **Configure**
   command (`./config.cmd --url ... --token ...`). It asks:
   - runner group: press Enter;
   - name: anything, e.g. `zpdf-pc`;
   - additional labels: type `zpdf`;
   - work folder: press Enter;
   - run as a service: type `N`. (A service runs as a different Windows
     account, without your Python, Node or a desktop for the browser tests.)
4. Start it with `C:\actions-runner\run.cmd`. It says "Listening for Jobs".
   Leave that window open while you want checks to run; close it to stop.
   To start it with Windows, put a shortcut to `run.cmd` in the folder that
   opens with `Win+R`, `shell:startup`.
5. Back on GitHub, open **Settings > Secrets and variables > Actions >
   Variables > New repository variable**: name `PC_RUNNER`, value `true`.
   From now on the tests go to your PC and the jobs that need GitHub's
   machines are skipped.

## Check it works

On GitHub, open **Actions > Tests > Run workflow**, pick the branch and run
it. The runner window shows "Running job: Windows PC / tests" and the run
turns green when it finishes (a few minutes).

If the job fails at once, without the runner window showing it, GitHub is
refusing to start any jobs for the account (for example a billing problem
rather than used-up minutes). A runner on the PC can't get round that; the
account's **Settings > Billing and plans** page says why.

## Turning it off

Delete the `PC_RUNNER` variable (or set it to `false`) to go back to
GitHub's machines. To remove the runner, run
`./config.cmd remove --token ...` with the token from the runner's page on
GitHub, then delete the folder.

## Safety

The runner runs whatever code is pushed to this repository, with your
Windows account's rights. That's fine here: the repository is private and
only you (and Claude, on your behalf) push to it. Don't connect this runner
to a public repository, where anyone could open a pull request.
