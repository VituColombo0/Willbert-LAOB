"""Acrescenta o modo Rosto ao SurfaceRenderer do SARndbox 2.8 (rodar na pasta da cópia)."""
import sys

def rep(s, old, new, count=1):
    if s.count(old) < 1:
        sys.exit(f"trecho não encontrado: {old[:70]!r}")
    return s.replace(old, new, count)

h = open("SurfaceRenderer.h").read()
if "FaceImage" not in h:
    h = rep(h, "class DEM;\n", "class DEM;\nclass FaceImage;\n")
    h = rep(h, "GLint heightMapShaderUniforms[16];", "GLint heightMapShaderUniforms[24];")
    h = rep(h, "\tGLfloat demDistScale; // Maximum deviation", "\tFaceImage* faceImage; // Foto projetada na areia (modo Rosto); 0 = desligado\n\tGLfloat demDistScale; // Maximum deviation")
    h = rep(h, "\tvoid setDemDistScale(", "\tvoid setFaceImage(FaceImage* newFaceImage); // Liga/desliga o modo Rosto\n\tvoid setDemDistScale(")
    open("SurfaceRenderer.h", "w").write(h)

c = open("SurfaceRenderer.cpp").read()
if "FaceImage" not in c:
    c = rep(c, '#include "DEM.h"\n', '#include "DEM.h"\n#include "FaceImage.h"\n')
    c = rep(c, "\t dem(0),demDistScale(1.0f),", "\t dem(0),faceImage(0),demDistScale(1.0f),")
    # vertex shader: coordenadas da foto e altura
    c = rep(c, """					heightColorMapTexCoord=dot(heightColorMapPlaneEq,vertexCc);\\n\\
					\\n";
				}""", """					heightColorMapTexCoord=dot(heightColorMapPlaneEq,vertexCc);\\n\\
					\\n";
				
				if(faceImage!=0)
					{
					/* Modo Rosto: posição na foto e altura da areia: */
					vertexUniforms+="\\
						uniform mat4 faceTransform; // Espaço da câmera -> (u, v, altura em cm)\\n";
					vertexVaryings+="\\
						varying vec3 faceTexCoord;\\n";
					vertexMain+="\\
						faceTexCoord=(faceTransform*vertexCc).xyz;\\n\\
						\\n";
					}
				}""")
    # fragment shader: cor da foto deformada pela altura e sombreada pelo relevo
    c = rep(c, """					vec4 baseColor=texture1D(heightColorMapSampler,heightColorMapTexCoord);\\n\\
					\\n";
""", """					vec4 baseColor=texture1D(heightColorMapSampler,heightColorMapTexCoord);\\n\\
					\\n";
				
				if(faceImage!=0)
					{
					fragmentUniforms+="\\
						uniform sampler2D faceSampler;\\n\\
						uniform vec4 faceParams; // ampliação/cm, deslocamento/cm, mistura, sombra\\n\\
						uniform float faceReference; // altura de referência da areia (cm)\\n";
					fragmentVaryings+="\\
						varying vec3 faceTexCoord;\\n";
					fragmentMain+="\\
						/* Modo Rosto: a areia alta amplia a foto (lente) e o relevo faz sombra: */\\n\\
						float faceH=faceTexCoord.z-faceReference;\\n\\
						vec2 faceUv=vec2(0.5)+(faceTexCoord.xy-vec2(0.5))*(1.0-clamp(faceParams.x*faceH,-0.8,0.8));\\n\\
						faceUv.x+=faceParams.y*faceH;\\n\\
						if(faceUv.x>=0.0&&faceUv.x<=1.0&&faceUv.y>=0.0&&faceUv.y<=1.0)\\n\\
							{\\n\\
							vec3 faceN=normalize(vec3(-dFdx(faceTexCoord.z)*faceParams.w*8.0,-dFdy(faceTexCoord.z)*faceParams.w*8.0,1.0));\\n\\
							float faceShade=clamp(0.45+0.75*dot(faceN,normalize(vec3(-0.5,0.5,0.7))),0.25,1.25);\\n\\
							vec4 faceColor=texture2D(faceSampler,faceUv);\\n\\
							baseColor=mix(vec4(faceColor.rgb*faceShade,1.0),baseColor,faceParams.z);\\n\\
							}\\n\\
						\\n";
					}
""")
    # localização dos uniforms (mesma ordem do envio)
    c = rep(c, """			*(ulPtr++)=glGetUniformLocationARB(result,"heightColorMapSampler");
			}""", """			*(ulPtr++)=glGetUniformLocationARB(result,"heightColorMapSampler");
			if(faceImage!=0)
				{
				*(ulPtr++)=glGetUniformLocationARB(result,"faceTransform");
				*(ulPtr++)=glGetUniformLocationARB(result,"faceSampler");
				*(ulPtr++)=glGetUniformLocationARB(result,"faceParams");
				*(ulPtr++)=glGetUniformLocationARB(result,"faceReference");
				}
			}""")
    c = rep(c, """		elevationColorMap->bindTexture(contextData);
		glUniform1iARB(*(ulPtr++),1);
		}
""", """		elevationColorMap->bindTexture(contextData);
		glUniform1iARB(*(ulPtr++),1);
		
		if(faceImage!=0)
			{
			/* Modo Rosto: matriz, foto (unidade 5), parâmetros e referência: */
			GLint tl=*(ulPtr++);
			GLint sl=*(ulPtr++);
			GLint pl=*(ulPtr++);
			GLint rl=*(ulPtr++);
			glActiveTextureARB(GL_TEXTURE5_ARB);
			faceImage->bindTexture(contextData);
			glUniform1iARB(sl,5);
			faceImage->uploadUniforms(tl,pl,rl);
			}
		}
""")
    c = rep(c, "void SurfaceRenderer::setDemDistScale(", """void SurfaceRenderer::setFaceImage(FaceImage* newFaceImage)
	{
	/* Ligar/desligar (ou trocar a foto) muda o shader: */
	faceImage=newFaceImage;
	++surfaceSettingsVersion;
	}

void SurfaceRenderer::setDemDistScale(""")
    open("SurfaceRenderer.cpp", "w").write(c)
print("SurfaceRenderer ok")
