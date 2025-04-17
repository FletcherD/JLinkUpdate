use clap::Parser;
use reqwest::blocking::Client;
use scraper::{Html, Selector};
use std::fs::File;
use std::io::Write;
use std::path::PathBuf;
use std::process::Command;
use regex::Regex;
use libloading::{Library, Symbol};
use indicatif::{ProgressBar, ProgressStyle};

#[derive(Parser, Debug)]
#[command(author, version, about)]
struct Args {
    /// Attempt to install using the system's package manager
    #[arg(long, default_value = "true")]
    install: bool,

    /// System architecture - 'auto' to autodetect
    #[arg(long, default_value = "auto")]
    #[arg(value_parser = ["auto", "x86_64", "i386", "arm", "arm64", "universal"])]
    arch: String,

    /// OS type - 'auto' to autodetect
    #[arg(long, default_value = "auto")]
    #[arg(value_parser = ["auto", "Linux", "MacOSX", "Windows"])]
    system: String,

    /// Package type to download - 'auto' to autodetect
    #[arg(long, default_value = "auto")]
    #[arg(value_parser = ["auto", "deb", "rpm", "tgz", "pkg", "exe"])]
    package_type: String,

    /// Call to package manager to install package - 'auto' to autodetect
    #[arg(long, default_value = "auto")]
    package_install_cmd: String,
}

#[derive(Debug)]
struct SystemInfo {
    arch: String,
    system: String,
    package_type: String,
    package_install_cmd: String,
}

fn get_current_installed_version(system: &str) -> Option<i32> {
    let dll_paths = match system {
        "Linux" => vec!["/opt/SEGGER/JLink*/libjlink*"],
        "Windows" => vec!["C:\\Program Files*\\SEGGER\\JLink*\\JLink*.dll"],
        "MacOSX" => vec!["/Applications/SEGGER/JLink*/libjlink*"],
        _ => return None,
    };

    for path in dll_paths {
        if let Ok(paths) = glob::glob(path) {
            for path in paths.flatten() {
                if let Ok(lib) = unsafe { Library::new(&path) } {
                    let func: Symbol<unsafe extern "C" fn() -> i32> = 
                        unsafe { lib.get(b"JLINK_GetDLLVersion") }.ok()?;
                    return Some(unsafe { func() });
                }
            }
        }
    }
    None
}

fn version_number_to_string(version: i32) -> String {
    let version_str = version.to_string();
    let major = &version_str[0..1];
    let minor = &version_str[1..3];
    let patch = version_str[3..].parse::<i32>().unwrap_or(0);
    
    let patch_str = if patch == 0 {
        String::new()
    } else {
        ((b'a' + (patch - 1) as u8) as char).to_string()
    };
    
    format!("V{}.{}{}", major, minor, patch_str)
}

fn version_string_to_number(version: &str) -> Option<i32> {
    let re = Regex::new(r"[vV](\d+)\.(\d+)([a-z])?").ok()?;
    let caps = re.captures(version)?;
    
    let major: i32 = caps.get(1)?.as_str().parse().ok()?;
    let minor: i32 = caps.get(2)?.as_str().parse().ok()?;
    let patch = caps.get(3)
        .map(|m| (m.as_str().chars().next().unwrap() as u8 - b'a' + 1) as i32)
        .unwrap_or(0);
    
    Some(major * 10000 + minor * 100 + patch)
}

fn get_system_info(args: &Args) -> Result<SystemInfo, Box<dyn std::error::Error>> {
    let system = if args.system == "auto" {
        std::env::consts::OS
    } else {
        args.system.as_str()
    };

    let (arch, system, package_type, mut package_install_cmd) = match system {
        "linux" => {
            let arch = if args.arch == "auto" {
                std::env::consts::ARCH.to_string()
            } else {
                args.arch.clone()
            };
            
            // Note: This is simplified. In a real implementation, you'd want to properly
            // detect the Linux distribution and package manager
            (arch, "Linux", "deb", "sudo dpkg -i")
        },
        "macos" => {
            ("universal".to_owned(), "MacOSX", "pkg", "sudo installer -target / -pkg")
        },
        "windows" => {
            let arch = if args.arch == "auto" {
                if cfg!(target_arch = "x86_64") {
                    "x86_64"
                } else {
                    ""
                }.to_string()
            } else {
                args.arch.clone()
            };
            (arch.to_owned(), "Windows", "exe", "")
        },
        _ => return Err("Unsupported system".into()),
    };

    let arch = match arch.to_lowercase().as_str() {
        "aarch64" => "arm64".to_string(),
        "amd64" => "x86_64".to_string(),
        _ => arch,
    };

    if args.package_install_cmd != "auto" {
        package_install_cmd = args.package_install_cmd.as_str();
    }

    Ok(SystemInfo {
        arch,
        system: system.to_string(),
        package_type: package_type.to_string(),
        package_install_cmd: package_install_cmd.to_string(),
    })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let system_info = get_system_info(&args)?;

    println!("Architecture: {}", system_info.arch);
    println!("System: {}", system_info.system);
    println!("Package Type: {}", system_info.package_type);
    println!("Package Install Command: {}", system_info.package_install_cmd);

    let client = Client::new();
    let jlink_url = "https://www.segger.com/downloads/jlink/";
    
    let response = client.get(jlink_url).send()?;
    let document = Html::parse_document(&response.text()?);
    let selector = Selector::parse("select.version").unwrap();
    let version_select = document.select(&selector).next()
        .ok_or("Could not find version selector")?;
    
    let latest_version = version_select.select(&Selector::parse("option").unwrap())
        .next()
        .ok_or("Could not find latest version")?
        .text()
        .next()
        .ok_or("Version text not found")?;

    let latest_version_number = version_string_to_number(&latest_version)
        .ok_or("Could not parse latest version number")?;
    
    println!("Latest Version: {} ({})", latest_version, latest_version_number);

    if let Some(current_version) = get_current_installed_version(&system_info.system) {
        println!("Installed version: {} ({})", 
                version_number_to_string(current_version), 
                current_version);
        
        if current_version >= latest_version_number {
            println!("Already on latest version.");
            return Ok(());
        }
    } else {
        println!("Installed version: None");
    }

    let filename = format!("JLink_{}_{}_{}.{}",
        system_info.system,
        latest_version.replace(".", ""),
        system_info.arch,
        system_info.package_type
    );
    
    let file_url = format!("{}{}", jlink_url, filename);
    
    let response = client.post(&file_url)
        .form(&[("accept_license_agreement", "accepted")])
        .send()?;

    if response.status() != 200 {
        return Err(format!("Got status code {} while requesting file from server",
                         response.status()).into());
    }

    let total_size = response.content_length().unwrap_or(0);
    let pb = ProgressBar::new(total_size);
    pb.set_style(ProgressStyle::default_bar()
        .template("{spinner:.green} [{elapsed_precise}] [{bar:40.cyan/blue}] {bytes}/{total_bytes} ({eta})")
        .unwrap());

    let mut file = File::create(&filename)?;
    let mut downloaded = 0u64;

    for chunk in response.bytes()?.chunks(1024) {
        file.write_all(chunk)?;
        downloaded = std::cmp::min(downloaded + chunk.len() as u64, total_size);
        pb.set_position(downloaded);
    }
    
    pb.finish_with_message("Download completed");

    if args.install {
        let status = if cfg!(target_os = "windows") {
            Command::new(&filename)
                .status()?
        } else {
            let install_cmd: Vec<&str> = system_info.package_install_cmd.split_whitespace().collect();
            Command::new(install_cmd[0])
                .args(&install_cmd[1..])
                .arg(PathBuf::from(&filename).canonicalize()?)
                .status()?
        };

        if !status.success() {
            return Err("Installation failed".into());
        }
    }

    println!("Success");
    Ok(())
}