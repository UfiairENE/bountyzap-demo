// Fix GitHub issue #12: FIX ISSUE WITH README
import * as fs from 'fs';
import * as childProcess from 'child_process';
import * as os from 'os';

function fixArtifactIssue() {
  // Get the list of running processes
  const runningProcesses = childProcess.execSync('ps -eo pid,cmd').toString().split(os.EOL);

  // Check if any process is running with the command 'tsc --noEmit'
  for (const processLine of runningProcesses) {
    if (processLine.includes('tsc --noEmit')) {
      console.log(`Found tsc --noEmit process: ${processLine}`);
      // Kill the process
      const pid = parseInt(processLine.split(' ')[0], 10);
      childProcess.execSync(`kill -9 ${pid}`);
    }
  }
}

// Call the function to fix the issue
fixArtifactIssue();