import re
import os
import xml.dom.minidom
import xml.sax.saxutils
import Utils.extensionutils as ext_utils
import Utils.constants as constants


from Utils.logger import default_logger as logger

def GetNodeTextData(a):
    """
    Filter non-text nodes from DOM tree
    """
    for b in a.childNodes:
        if b.nodeType == b.TEXT_NODE:
            return b.data


class OvfEnv(object):
    """
    Read, and process provisioning info from provisioning file OvfEnv.xml
    """

    #
    # <?xml version="1.0" encoding="utf-8"?>
    # <Environment xmlns="http://schemas.dmtf.org/ovf/environment/1" xmlns:oe="http://schemas.dmtf.org/ovf/environment/1" xmlns:wa="http://schemas.microsoft.com/windowsazure" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    #    <wa:ProvisioningSection>
    #      <wa:Version>1.0</wa:Version>
    #      <LinuxProvisioningConfigurationSet xmlns="http://schemas.microsoft.com/windowsazure" xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
    #        <ConfigurationSetType>LinuxProvisioningConfiguration</ConfigurationSetType>
    #        <HostName>HostName</HostName>
    #        <UserName>UserName</UserName>
    #        <UserPassword>UserPassword</UserPassword>
    #        <DisableSshPasswordAuthentication>false</DisableSshPasswordAuthentication>
    #        <SSH>
    #          <PublicKeys>
    #            <PublicKey>
    #              <Fingerprint>EB0C0AB4B2D5FC35F2F0658D19F44C8283E2DD62</Fingerprint>
    #              <Path>$HOME/UserName/.ssh/authorized_keys</Path>
    #            </PublicKey>
    #          </PublicKeys>
    #          <KeyPairs>
    #            <KeyPair>
    #              <Fingerprint>EB0C0AB4B2D5FC35F2F0658D19F44C8283E2DD62</Fingerprint>
    #              <Path>$HOME/UserName/.ssh/id_rsa</Path>
    #            </KeyPair>
    #          </KeyPairs>
    #        </SSH>
    #      </LinuxProvisioningConfigurationSet>
    #    </wa:ProvisioningSection>
    # </Environment>
    #
    def __init__(self):
        """
        Reset members.
        """
        self.WaNs = "http://schemas.microsoft.com/windowsazure"
        self.OvfNs = "http://schemas.dmtf.org/ovf/environment/1"
        self.MajorVersion = 1
        self.MinorVersion = 0
        self.ComputerName = None
        self.AdminPassword = None
        self.UserName = None
        self.UserPassword = None
        self.CustomData = None
        self.DisableSshPasswordAuthentication = True
        self.SshPublicKeys = []
        self.SshKeyPairs = []

    # this is a static function to return an instance of  OfvEnv
    def parse(xmlText, configuration, isDeprovision=False):
        """
        Parse xml tree, retreiving user and ssh key information.
        Return self.
        """
        ofv_env = OvfEnv()
        logger.LogIfVerbose(re.sub("<UserPassword>.*?<", "<UserPassword>*<", xmlText))
        dom = xml.dom.minidom.parseString(xmlText)
        if len(dom.getElementsByTagNameNS(ofv_env.OvfNs, "Environment")) != 1:
            logger.Error("Unable to parse OVF XML.")
        section = None
        newer = False
        for p in dom.getElementsByTagNameNS(ofv_env.WaNs, "ProvisioningSection"):
            for n in p.childNodes:
                if n.localName == "Version":
                    verparts = GetNodeTextData(n).split('.')
                    major = int(verparts[0])
                    minor = int(verparts[1])
                    if major > ofv_env.MajorVersion:
                        newer = True
                    if major != ofv_env.MajorVersion:
                        break
                    if minor > ofv_env.MinorVersion:
                        newer = True
                    section = p
        if newer:
            logger.Warn("Newer provisioning configuration detected. Please consider updating waagent.")
        if section is None:
            logger.Error("Could not find ProvisioningSection with major version=" + str(ofv_env.MajorVersion))
            return None
        ofv_env.ComputerName = GetNodeTextData(section.getElementsByTagNameNS(ofv_env.WaNs, "HostName")[0])
        ofv_env.UserName = GetNodeTextData(section.getElementsByTagNameNS(ofv_env.WaNs, "UserName")[0])
        if isDeprovision:
            return ofv_env
        try:
            ofv_env.UserPassword = GetNodeTextData(section.getElementsByTagNameNS(ofv_env.WaNs, "UserPassword")[0])
        except:
            pass
        cd_section = None
        try:
            cd_section = section.getElementsByTagNameNS(ofv_env.WaNs, "CustomData")
            if len(cd_section) > 0:
                ofv_env.CustomData = GetNodeTextData(cd_section[0])
                if len(ofv_env.CustomData) > 0:
                    ext_utils.set_file_contents(constants.LibDir + '/CustomData', bytearray(
                        ext_utils.translate_custom_data(ofv_env.CustomData, configuration)))
                    logger.Log('Wrote ' + constants.LibDir + '/CustomData')
                else:
                    logger.Error('<CustomData> contains no data!')
        except Exception as e:
            logger.Error(str(e) + ' occured creating ' + constants.LibDir + '/CustomData')
        disable_ssh_passwd = section.getElementsByTagNameNS(ofv_env.WaNs, "DisableSshPasswordAuthentication")
        if len(disable_ssh_passwd) != 0:
            ofv_env.DisableSshPasswordAuthentication = (GetNodeTextData(disable_ssh_passwd[0]).lower() == "true")
        for pkey in section.getElementsByTagNameNS(ofv_env.WaNs, "PublicKey"):
            logger.LogIfVerbose(repr(pkey))
            fp = None
            path = None
            for c in pkey.childNodes:
                if c.localName == "Fingerprint":
                    fp = GetNodeTextData(c).upper()
                    logger.LogIfVerbose(fp)
                if c.localName == "Path":
                    path = GetNodeTextData(c)
                    logger.LogIfVerbose(path)
            ofv_env.SshPublicKeys += [[fp, path]]
        for keyp in section.getElementsByTagNameNS(ofv_env.WaNs, "KeyPair"):
            fp = None
            path = None
            logger.LogIfVerbose(repr(keyp))
            for c in keyp.childNodes:
                if c.localName == "Fingerprint":
                    fp = GetNodeTextData(c).upper()
                    logger.LogIfVerbose(fp)
                if c.localName == "Path":
                    path = GetNodeTextData(c)
                    logger.LogIfVerbose(path)
            ofv_env.SshKeyPairs += [[fp, path]]
        return ofv_env

    def prepare_dir(self, filepath, distro):
        """
        Create home dir for self.UserName
        Change owner and return path.
        """
        home = distro.get_home()
        # Expand HOME variable if present in path
        path = os.path.normpath(filepath.replace("$HOME", home))
        if (not path.startswith("/")) or path.endswith("/"):
            return None
        dir_name = path.rsplit('/', 1)[0]
        if dir_name != "":
            ext_utils.create_dir(dir_name, "root", 0o700)
            if path.startswith(os.path.normpath(home + "/" + self.UserName + "/")):
                ext_utils.create_dir(dir_name, self.UserName)
        return path