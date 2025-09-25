import datetime
import glob
import os
import platform
import shutil
import subprocess
import sys
import time
from typing import List
import urllib.request
import winreg
import zipfile

from baserunner import Runner, main


class WinRunner(Runner):
    """
    Windows runner
    """

    def __init__(self, path: str, args: List[str], **kwargs):
        super().__init__(path, args, **kwargs)

        self.cdb_exe = self.find_cdb()
        if not self.cdb_exe:
            raise Exception('Could not find cdb.exe')
        
        self.procdump_exe = self.find_procdump()
        if not self.procdump_exe:
            raise Exception('Could not find procdump.exe')
        self.procdump_exe = os.path.abspath(self.procdump_exe)

    @classmethod
    def search_cdb(cls, start_dir: str, machine_type: str = None) -> List[str]:
        """
        Find cdb.exe suitable for current platform
        """
        results = []
        ndirs = 0

        if not machine_type:
            machine_type = platform.machine()
            if machine_type == 'AMD64' or machine_type == 'x86_64':
                machine_type = 'x64'
            else:
                machine_type = 'x86'

        for root, dirs, files in os.walk(start_dir):
            cdbs = [os.path.join(root, f) for f in files
                    if 'cdb'==f.lower()[:3] and '.exe'==f.lower()[-4:]
                    ]
            #cdbs = [path for path in cdbs
            #        if machine_type in path.lower()
            #        ]
            for path in cdbs:
                print(path)
            results += cdbs
            ndirs += 1

        #print(f'Walked {ndirs} directories and found {len(results)} result(s)')
        return results


    @classmethod
    def find_cdb(cls) -> str:
        """
        Find cdb.exe (console debugger).
        It can be installed from Windows SDK installer:
        1a. Run Windows SDK 10 installer if you haven't installed it
         b. If you have installed it, run Windows SDK installer from Add/Remove Program ->
            Windows Software Development Kit -> Modify
        2. Select component: "Debugging Tools for Windows"
        """
        CDB_PATHS = [
            r'C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe'
        ]
        for path in CDB_PATHS:
            if os.path.exists(path):
                return path
            
        return None

    @classmethod
    def find_procdump(cls) -> str:
        """
        Find procdump.exe.
        See https://learn.microsoft.com/en-us/sysinternals/downloads/procdump
        """
        path = shutil.which('procdump')
        if path:
            return path

        # find procdump.exe in same directory as this script
        dir = os.path.dirname(__file__)
        path = os.path.join(dir, 'procdump.exe')
        if os.path.exists(path):
            return path
    
        # giving up
        return None

    @classmethod
    def get_dump_dir(cls) -> str:
        """Get the actual path of the dump directory that is installed in the registry"""
        home = os.environ['userprofile']
        return os.path.abspath( os.path.join(home, 'Dumps') )

    @classmethod
    def get_dump_pattern(cls) -> str:
        """
        Get file pattern to find dump files
        """
        return "*.dmp"

    @classmethod
    def install(cls):
        """Requires administrator privilege to write to registry"""
        

        #
        # Setup registry to tell Windows to create minidump on app crash.
        # https://learn.microsoft.com/en-us/windows/win32/wer/collecting-user-mode-dumps
        #
        HKLM = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
        LD = r'SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps'
        try:
            ld = winreg.OpenKey(HKLM, LD)
        except OSError as e:
            ld = winreg.CreateKey(HKLM, LD)
            cls.info(f'Registry "LocalDumps" key created')
        
        dump_dir = cls.get_dump_dir()
        if not os.path.exists(dump_dir):
            os.makedirs(dump_dir)
            cls.info(f'Directory {dump_dir} created')

        DUMP_FOLDER = '%userprofile%\Dumps'
        try:
            val, type = winreg.QueryValueEx(ld, 'DumpFolder')
        except OSError as e:
            val, type = '', None
        if val.lower() != DUMP_FOLDER.lower() or type != winreg.REG_EXPAND_SZ:
            winreg.SetValueEx(ld, 'DumpFolder', None, winreg.REG_EXPAND_SZ, DUMP_FOLDER)
            cls.info(f'Registry "DumpFolder" set to {DUMP_FOLDER}')

        try:
            val, type = winreg.QueryValueEx(ld, 'DumpType')
        except OSError as e:
            val, type = -1, None
        MINIDUMP = 1
        if val!=MINIDUMP or type!=winreg.REG_DWORD:
            winreg.SetValueEx(ld, 'DumpType', None, winreg.REG_DWORD, MINIDUMP)
            cls.info(f'Registry "DumpType" set to {MINIDUMP}')

        winreg.CloseKey(ld)

        # Check cdb.exe and procdump.exe
        errors = []
        cdb_exe = cls.find_cdb()
        if not cdb_exe:
            errors.append('cdb.exe not found')
            cls.info('cdb.exe not found, searching for it..')
            cbds = cls.search_cdb('C:\\')
            if cbds:
                errors.append(f'Potential cdb.exe or similar file can be found here: {", ".join(cbds)}')
            else:
                cls.info('No potential cdb.exe replacement was found')

        procdump_exe = cls.find_procdump()
        if not procdump_exe:
            cls.info('Downloading procdump.zip..')
            try:
                urllib.request.urlretrieve("https://download.sysinternals.com/files/Procdump.zip",
                                           "procdump.zip")
            except:
                cls.info('Download failed, using cached version..')
                shutil.copyfile('.cache/procdump.zip', 'procdump.zip')
            cls.info('Extracting procdump.exe..')
            with zipfile.ZipFile('procdump.zip', 'r') as zip_ref:
                zip_ref.extract('procdump.exe')

        procdump_exe = cls.find_procdump()
        if not procdump_exe:
            errors.append('procdump.exe not found')


        if errors:
            cls.err('ERROR: ' + ' '.join(errors))
            sys.exit(1)

        cls.info('Running infrastructure is ready')

    def get_dump_path(self) -> str:
        dump_dir = self.get_dump_dir()
        basename = os.path.basename(self.path)
        dump_file = f'{basename}.{self.popen.pid}.dmp'
        return os.path.join(dump_dir, dump_file)
        
    def terminate(self):
        """
        Terminate a process and generate dump file
        """
        
        # procdump default dump filename is PROCESSNAME_YYMMDD_HHMMDD.
        # Since guessing the datetime can be unreliable, let's create
        # a temporary directory for procdump to store the dumpfile.
        dtime = datetime.datetime.now()
        temp_dir = os.path.join( self.get_dump_dir(), f'ci-runner-{dtime.strftime("%y%m%d-%H%M%S")}')
        os.makedirs(temp_dir)

        # Run procdump to dump the process
        procdump_p = subprocess.Popen([
                    self.procdump_exe,
                    '-accepteula', '-o', 
                    f'{self.popen.pid}',
                ],
                cwd=temp_dir,
                )
        procdump_p.wait()
        
        # We can now terminate the process
        time.sleep(1)
        self.popen.terminate()
        
        # Get the dump file
        files = glob.glob( os.path.join(temp_dir, "*.dmp") )
        if not files:
            self.err("ERROR: unable to find dump file(s) generated by procdump")
            raise Exception('procdump dump file not found')

        # Copy and rename the procdump's dump file to standard dump file location/name
        dump_file = files[-1]
        shutil.copyfile(dump_file, self.get_dump_path())

        # Don't need the temp dir anymore
        shutil.rmtree(temp_dir)

    def process_crash(self):
        """
        Process dump file.
        """
        dump_path = self.get_dump_path()
        
        # Execute cdb to print crash info
        args = [
            self.cdb_exe,
            '-z',
            dump_path,
            '-c',
            '!analyze -v; ~* k; q',
        ]
        self.info(' '.join(args))
        cdb = subprocess.Popen(args)  # , stdout=sys.stderr
        cdb.wait()

    def get_additional_artifacts(self) -> List[str]:
        """
        Return list of files to be uploaded as additional artifacts when
        the program crashed and artifact_dir is set.
        """
        exe = os.path.abspath(self.path)
        pdb = os.path.join(
            os.path.dirname(exe),
            os.path.splitext(os.path.basename(exe))[0] + '.pdb'
        )
        if os.path.exists(pdb):
            return [pdb]
        return []


if __name__ == '__main__':
    main(WinRunner)
